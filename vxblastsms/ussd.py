import json
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.etree import ElementTree
from xml.dom import minidom

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
    TRANSPORT_TYPE = 'ussd'
    TRANSPORT_NAME = 'vumi-blastsms'
    REQUEST_TYPE = {
        'request': '1',  # new ussd request
        'response': '2',  # response in already existing session
        'release': '3',  # end of session
        'timeout': '4'  # timeout
    }
    ENCODING = 'utf-8'
    EXPECTED_FIELDS = set(['msisdn', 'shortcode', 'sessionid', 'type'])
    OPTIONAL_FIELDS = set(['msg', 'appid'])

    # errors
    RESPONSE_FAILURE_ERROR = "Response to http request failed."
    NOT_REPLY_ERROR = "Outbound message is not a reply"
    NO_CONTENT_ERROR = "Outbound message has no content."

    CONFIG_CLASS = BlastSMSUssdTransportConfig

    def get_field_values(self, request_data, expected_fields,
                         ignored_fields=frozenset()):
        values = {}
        errors = {}

        for field in request_data:
            if field not in (expected_fields | ignored_fields):
                if self._validation_mode == self.STRICT_MODE:
                    errors.setdefault('unexpected_parameter', []).append(field)
            else:
                values[field] = (
                    request_data[field].decode(self.ENCODING))
        for field in expected_fields:
            if field not in values:
                errors.setdefault('missing_parameter', []).append(field)

        return values, errors

    def get_optional_field_values(self, request_data,
                                  optional_fields=frozenset()):
        values = {}

        for field in optional_fields:
            if field in request_data:
                raw_value = request_data[field]
                values[field] = raw_value.decode(self.ENCODING)
            else:
                values[field] = None

        return values

    def get_request_data_dict(self, request):
        req_content = request.content.read()
        req_data = {}

        root = ElementTree.fromstring(req_content)
        for subel in root:
            if subel.text is not None:
                req_data[subel.tag] = subel.text

        return req_data

    @inlineCallbacks
    def handle_raw_inbound_message(self, message_id, request):
        request_data = self.get_request_data_dict(request)

        values, errors = self.get_field_values(
            request_data,
            self.EXPECTED_FIELDS,
            self.OPTIONAL_FIELDS,  # pass this in for error checking
        )

        optional_values = self.get_optional_field_values(
            request_data,
            self.OPTIONAL_FIELDS
        )

        if errors:
            log.info('Unhappy incoming message: %s ' % (errors,))
            yield self.finish_request(
                message_id, json.dumps(errors), code=http.BAD_REQUEST
            )
            return

        if values['type'] == self.REQUEST_TYPE['response']:  # resume session
            session_event = TransportUserMessage.SESSION_RESUME
        elif values['type'] == self.REQUEST_TYPE['request']:  # new session
            session_event = TransportUserMessage.SESSION_NEW
        else:
            # TODO #4: handle self.REQUEST_TYPE['release'] and
            # self.REQUEST_TYPE['timeout'].
            # Default to new session for now.
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
            session_event=session_event,
            transport_type=self.TRANSPORT_TYPE,
            transport_name=self.TRANSPORT_NAME,
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

        # Set request type
        se_type = SubElement(e_ussdresp, 'type')
        if session_event != TransportUserMessage.SESSION_CLOSE:
            se_type.text = self.REQUEST_TYPE['response']
        else:
            se_type.text = self.REQUEST_TYPE['release']

        se_msg = SubElement(e_ussdresp, 'msg')
        se_msg.text = reply_content

        return minidom.parseString(tostring(
            e_ussdresp,
            encoding='utf-8',
        )).toxml(encoding="utf-8")

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
