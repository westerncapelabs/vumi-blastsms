import json
from urllib import quote
from xml.etree.ElementTree import Element, SubElement, tostring

from twisted.internet.defer import inlineCallbacks
from twisted.web import http

from vumi.message import TransportUserMessage
from vumi.config import ConfigText, ConfigDict
from vumi.transports.httprpc import HttpRpcTransport
from vumi import log


class BlastSMSUssdTransportConfig(HttpRpcTransport.CONFIG_CLASS):
    base_url = ConfigText(
        'The base url of the transport',
        required=True, static=True)
    provider_mappings = ConfigDict(
        'Mappings from the provider values received from BlastSMS to '
        'normalised provider values',
        static=True, default={})


class BlastSMSUssdTransport(HttpRpcTransport):
    """
    HTTP transport for USSD with BlastSMS
    """
    transport_type = 'ussd'
    transport_name = 'vumi-blastsms'
    ENCODING = 'utf-8'
    EXPECTED_FIELDS = set(['msisdn', 'provider', 'type', 'shortcode'])
    OPTIONAL_FIELDS = set(['request', 'appid', 'to_addr'])

    # errors
    RESPONSE_FAILURE_ERROR = "Response to http request failed."
    NOT_REPLY_ERROR = "Outbound message is not a reply"
    NO_CONTENT_ERROR = "Outbound message has no content."

    CONFIG_CLASS = BlastSMSUssdTransportConfig

    @inlineCallbacks
    def setup_transport(self):
        yield super(BlastSMSUssdTransport, self).setup_transport()
        config = self.get_static_config()
        self.provider_mappings = config.provider_mappings

    def get_callback_url(self, to_addr):
        config = self.get_static_config()
        return "%s%s?to_addr=%s" % (
            config.base_url.rstrip("/"),
            config.web_path,
            quote(to_addr))

    def get_optional_field_values(self, request, optional_fields=frozenset()):
        values = {}
        for field in optional_fields:
            if field in request.args:
                raw_value = request.args.get(field)[0]
                values[field] = raw_value.decode(self.ENCODING)
            else:
                values[field] = None
        # print(values)
        return values

    def normalise_provider(self, provider):
        if provider not in self.provider_mappings:
            log.warning(
                "No mapping exists for provider '%s', "
                "using '%s' as a fallback" % (provider, provider,))
            return provider
        else:
            return self.provider_mappings[provider]

    @inlineCallbacks
    def handle_raw_inbound_message(self, message_id, request):
        values, errors = self.get_field_values(
            request,
            self.EXPECTED_FIELDS,
            self.OPTIONAL_FIELDS,  # pass this in for error checking
        )
        print('values')
        print(values)
        print('values')
        print('')

        print('errors')
        print(errors)
        print('errors')
        print('')

        optional_values = self.get_optional_field_values(
            request,
            self.OPTIONAL_FIELDS
        )
        print('optional_values')
        print(optional_values)
        print('optional_values')
        print('')

        if errors:
            log.info('Unhappy incoming message: %s ' % (errors,))
            yield self.finish_request(
                message_id, json.dumps(errors), code=http.BAD_REQUEST
            )
            return

        from_addr = values['msisdn']
        provider = self.normalise_provider(values['provider'])

        ussd_appid = optional_values['appid']

        if values['type'] == '2':  # resume session
            session_event = TransportUserMessage.SESSION_RESUME
        elif values['type'] == '1':  # new session
            session_event = TransportUserMessage.SESSION_NEW
        else:
            # TODO: handle other types if necessary
            # Default to new session for 3 & 4 for now
            session_event = TransportUserMessage.SESSION_NEW

        if optional_values['to_addr'] is not None:
            to_addr = optional_values['to_addr']
            content = optional_values['request']
        else:
            to_addr = optional_values['request']
            content = None

        log.info(
            'BlastSMSUssdTransport receiving inbound message from %s to '
            '%s.' % (from_addr, to_addr))

        yield self.publish_message(
            message_id=message_id,
            content=content,
            to_addr=to_addr,
            from_addr=from_addr,
            provider=provider,
            session_event=session_event,
            transport_type=self.transport_type,
            # transport_name=self.transport_name,  # ?
            transport_metadata={
                'sessionid': 'var_sessionid',
                'appid': ussd_appid,

            },
        )

    def generate_body(self, msisdn, sessionid, appid, in_reply_to,
                      reply_content, session_event):

        e_ussdresp = Element('ussdresp')

        se_msisdn = SubElement(e_ussdresp, 'msisdn')
        se_msisdn.text = msisdn

        se_sessionid = SubElement(e_ussdresp, 'sessionid')
        se_sessionid.text = sessionid
        # if sessionid
        #     se_sessionid.text = sessionid
        # else:
        #     se_sessionid.text = ''

        se_appid = SubElement(e_ussdresp, 'appid')
        if appid is not None:
            se_appid.text = appid
        else:
            # provide application session id - use reference to inbound
            # message that started the session
            se_appid.text = in_reply_to

        # Set Message Type: 2 for Response, 3 for Release
        se_type = SubElement(e_ussdresp, 'type')
        if session_event != TransportUserMessage.SESSION_CLOSE:
            se_type.text = '2'
        else:
            se_type.text = '3'

        se_msg = SubElement(e_ussdresp, 'msg')
        se_msg.text = reply_content

        return tostring(
            e_ussdresp,
            encoding='utf-8',
        )

    @inlineCallbacks
    def handle_outbound_message(self, message):

        # Generate outbound message
        message_id = message['message_id']
        body = self.generate_body(
            message['to_addr'],  # msisdn
            message['transport_metadata']['sessionid'],  # sessionid
            message['transport_metadata']['appid'],  # appid
            message['in_reply_to'],
            message['content'],
            message['session_event']
        )
        log.info('BlastSMSUssdTransport outbound message with content: %r'
                 % (body,))

        # Errors
        if not message['content']:
            yield self.publish_nack(message_id, self.NO_CONTENT_ERROR)
            return
        if not message['in_reply_to']:
            yield self.publish_nack(message_id, self.NOT_REPLY_ERROR)
            return

        # Finish Request
        response_id = self.finish_request(
            message['in_reply_to'],
            body,
        )

        # Response failure
        if response_id is None:
            # we don't yield on this publish because if a message store is
            # used, that causes the worker to wait for Riak before processing
            # the message and responding to USSD messages is time critical.
            self.publish_nack(message_id, self.RESPONSE_FAILURE_ERROR)
            return

        # we don't yield on this publish because if a message store is
        # used, that causes the worker to wait for Riak before processing
        # the message and responding to USSD messages is time critical.
        self.publish_ack(
            user_message_id=message_id, sent_message_id=message_id)
