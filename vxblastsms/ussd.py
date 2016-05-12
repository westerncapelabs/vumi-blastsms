import json
from xml.etree.ElementTree import Element, SubElement, tostring

from twisted.internet.defer import inlineCallbacks
from twisted.web import http

from vumi.message import TransportUserMessage
from vumi.transports.httprpc import HttpRpcTransport
from vumi.config import ConfigText
from vumi import log


class BlastSMSUssdTransportConfig(HttpRpcTransport.CONFIG_CLASS):
    app_id = ConfigText(
        'App identifying string',
        required=False, static=True)


class BlastSMSUssdTransport(HttpRpcTransport):
    """
    HTTP transport for USSD with BlastSMS
    """
    transport_type = 'ussd'
    transport_name = 'vumi-blastsms'
    ENCODING = 'utf-8'
    EXPECTED_FIELDS = set(['msisdn', 'shortcode', 'sessionid', 'type'])
    OPTIONAL_FIELDS = set(['msg', 'appid'])

    # errors
    RESPONSE_FAILURE_ERROR = "Response to http request failed."
    NOT_REPLY_ERROR = "Outbound message is not a reply"
    NO_CONTENT_ERROR = "Outbound message has no content."

    CONFIG_CLASS = BlastSMSUssdTransportConfig

    def get_optional_field_values(self, request, optional_fields=frozenset()):
        values = {}
        for field in optional_fields:
            if field in request.args:
                raw_value = request.args.get(field)[0]
                values[field] = raw_value.decode(self.ENCODING)
            else:
                values[field] = None
        return values

    @inlineCallbacks
    def handle_raw_inbound_message(self, message_id, request):
        values, errors = self.get_field_values(
            request,
            self.EXPECTED_FIELDS,
            self.OPTIONAL_FIELDS,  # pass this in for error checking
        )

        optional_values = self.get_optional_field_values(
            request,
            self.OPTIONAL_FIELDS
        )

        if errors:
            log.info('Unhappy incoming message: %s ' % (errors,))
            yield self.finish_request(
                message_id, json.dumps(errors), code=http.BAD_REQUEST
            )
            return

        if values['type'] == '2':  # resume session
            session_event = TransportUserMessage.SESSION_RESUME
        elif values['type'] == '1':  # new session
            session_event = TransportUserMessage.SESSION_NEW
        else:
            # TODO: handle other types if necessary
            # Default to new session for 3 & 4 for now
            session_event = TransportUserMessage.SESSION_NEW

        if optional_values['msg'] is not None:
            content = optional_values['msg']
        else:
            content = None

        log.info(
            'BlastSMSUssdTransport receiving inbound message from %s to '
            '%s.' % (values['msisdn'], values['shortcode']))

        yield self.publish_message(
            message_id=message_id,
            content=content,
            to_addr=values['shortcode'],
            from_addr=values['msisdn'],
            # provider=None,
            session_event=session_event,
            transport_type=self.transport_type,
            transport_name=self.transport_name,
            transport_metadata={
                'sessionid': values['sessionid'],
                'appid': optional_values['appid'],
            },
        )

    def generate_body(self, msisdn, sessionid, appid, in_reply_to,
                      reply_content, session_event):

        e_ussdresp = Element('ussdresp')

        se_msisdn = SubElement(e_ussdresp, 'msisdn')
        se_msisdn.text = msisdn

        se_sessionid = SubElement(e_ussdresp, 'sessionid')
        se_sessionid.text = sessionid

        se_appid = SubElement(e_ussdresp, 'appid')
        if appid is not None:
            se_appid.text = appid
        else:
            config = self.get_static_config()
            if config.app_id:
                se_appid.text = config.app_id
            else:
                se_appid.text = None

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

        # Errors
        message_id = message['message_id']
        if not message['content']:
            yield self.publish_nack(message_id, self.NO_CONTENT_ERROR)
            return
        if not message['in_reply_to']:
            yield self.publish_nack(message_id, self.NOT_REPLY_ERROR)
            return

        # Generate outbound message
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
