# -*- coding: utf-8 -*-
import json
from xml.etree.ElementTree import Element, SubElement, tostring

from twisted.internet.defer import inlineCallbacks

from vumi.message import TransportUserMessage
from vumi.tests.helpers import VumiTestCase
from vumi.transports.httprpc.tests.helpers import HttpRpcTransportHelper

from vxblastsms.ussd import BlastSMSUssdTransport


class TestBlastSMSUssdTransport(VumiTestCase):

    defaults = {
        'msisdn': '273334444',
        'shortcode': '8864',
        'sessionid': 'test_session_id',
        'type': '2'
    }

    def setUp(self):
        self.tx_helper = self.add_helper(
            HttpRpcTransportHelper(
                BlastSMSUssdTransport
            )
        )

    def get_transport(self, config={}):
        defaults = {
            'app_id': 'test_config_app_id',
            'web_path': '/api/blastSMS/ussd/',
            'web_port': '0',
        }
        defaults.update(config)
        return self.tx_helper.get_transport(defaults)

    def make_inbound_xml_string(self, msisdn=defaults['msisdn'],
                                shortcode=defaults['shortcode'],
                                sessionid=defaults['sessionid'],
                                type=defaults['type'],
                                msg=None, appid=None):
        e_ussdresp = Element('ussdresp')

        se_msisdn = SubElement(e_ussdresp, 'msisdn')
        se_msisdn.text = msisdn
        se_shortcode = SubElement(e_ussdresp, 'shortcode')
        se_shortcode.text = shortcode
        se_sessionid = SubElement(e_ussdresp, 'sessionid')
        se_sessionid.text = sessionid
        se_type = SubElement(e_ussdresp, 'type')
        se_type.text = type
        se_msg = SubElement(e_ussdresp, 'msg')
        se_msg.text = msg
        se_appid = SubElement(e_ussdresp, 'appid')
        se_appid.text = appid

        return tostring(e_ussdresp, encoding='utf-8')

    def assert_inbound_message(self, msg, **field_values):
        expected_field_values = {
            'content': "",
            'from_addr': self.defaults['msisdn'],
        }
        expected_field_values.update(field_values)
        for field, expected_value in expected_field_values.iteritems():
            self.assertEqual(msg[field], expected_value)

    def assert_outbound_message(self, outbound_msg, appid, config_app_id,
                                sessionid, reply_content, msisdn,
                                continue_session=True):
        se_msisdn = '<msisdn>%s</msisdn>' % str(msisdn)
        se_sessionid = '<sessionid>%s</sessionid>' % str(sessionid)
        se_msg = '<msg>%s</msg>' % str(reply_content)

        if appid is None:
            if config_app_id is None:
                se_appid = None
            else:
                se_appid = '<appid>%s</appid>' % str(config_app_id)
        else:
            se_appid = '<appid>%s</appid>' % str(appid)

        if continue_session:
            se_type = '<type>%s</type>' % '2'
        else:
            se_type = '<type>%s</type>' % '3'

        xml = ''.join([
            '<ussdresp>',
            se_msisdn, se_sessionid, se_appid, se_type, se_msg,
            '</ussdresp>'
        ])

        self.assertEqual(outbound_msg, xml)

    def assert_ack(self, ack, reply):
        self.assertEqual(ack.payload['event_type'], 'ack')
        self.assertEqual(ack.payload['user_message_id'], reply['message_id'])
        self.assertEqual(ack.payload['sent_message_id'], reply['message_id'])

    def assert_nack(self, nack, reply, reason):
        self.assertEqual(nack.payload['event_type'], 'nack')
        self.assertEqual(nack.payload['user_message_id'], reply['message_id'])
        self.assertEqual(nack.payload['nack_reason'], reason)

    @inlineCallbacks
    def test_inbound_begin(self):
        yield self.get_transport()
        ussd_string = "*1234#"

        inbound_xml = self.make_inbound_xml_string(
            shortcode=ussd_string, type='1')

        d = self.tx_helper.mk_request(_data=inbound_xml, _method='POST')
        [msg] = yield self.tx_helper.wait_for_dispatched_inbound(1)

        self.assert_inbound_message(
            msg,
            session_event=TransportUserMessage.SESSION_NEW,
            to_addr=ussd_string,
            content=None,
        )

        reply_content = 'We are the Knights Who Say ... Ni!'
        reply = msg.reply(reply_content)
        self.tx_helper.dispatch_outbound(reply)
        response = yield d

        self.assert_outbound_message(
            response.delivered_body,  # outbound_msg
            None,  # appid
            'test_config_app_id',  # config app id
            msg['transport_metadata']['sessionid'],  # session_id
            reply_content,  # expected content in reply
            msg['from_addr']  # msisdn
        )

        [ack] = yield self.tx_helper.wait_for_dispatched_events(1)
        self.assert_ack(ack, reply)

    @inlineCallbacks
    def test_inbound_begin_with_close(self):
        yield self.get_transport()
        ussd_string = "*code#"

        # Send initial request
        inbound_xml = self.make_inbound_xml_string(
            shortcode=ussd_string, type='1')

        d = self.tx_helper.mk_request(_data=inbound_xml, _method='POST')
        [msg] = yield self.tx_helper.wait_for_dispatched_inbound(1)

        self.assert_inbound_message(
            msg,
            session_event=TransportUserMessage.SESSION_NEW,
            content=None,
        )

        reply_content = 'We are no longer the Knight who say Ni!'
        reply = msg.reply(reply_content, continue_session=False)
        self.tx_helper.dispatch_outbound(reply)
        response = yield d

        self.assert_outbound_message(
            response.delivered_body,
            None,  # appid
            'test_config_app_id',  # config app id
            msg['transport_metadata']['sessionid'],
            reply_content,
            msg['from_addr'],  # msisdn
            continue_session=False,
        )

        [ack] = yield self.tx_helper.wait_for_dispatched_events(1)
        self.assert_ack(ack, reply)

    @inlineCallbacks
    def test_inbound_resume_and_reply_with_end(self):
        yield self.get_transport()

        ussd_string = "*1234#"
        user_content = "I didn't expect a kind of Spanish Inquisition!"
        inbound_xml = self.make_inbound_xml_string(
            shortcode=ussd_string, msg=user_content, appid='provided_app_id')

        d = self.tx_helper.mk_request(_data=inbound_xml, _method='POST')
        [msg] = yield self.tx_helper.wait_for_dispatched_inbound(1)
        self.assert_inbound_message(
            msg,
            session_event=TransportUserMessage.SESSION_RESUME,
            content=user_content,
        )

        reply_content = "Nobody expects the Spanish Inquisition!"
        reply = msg.reply(reply_content, continue_session=False)
        self.tx_helper.dispatch_outbound(reply)
        response = yield d

        self.assert_outbound_message(
            response.delivered_body,
            'provided_app_id',  # appid
            'test_config_app_id',  # config app id
            msg['transport_metadata']['sessionid'],
            reply_content,
            msg['from_addr'],  # msisdn
            continue_session=False,
        )

        [ack] = yield self.tx_helper.wait_for_dispatched_events(1)
        self.assert_ack(ack, reply)

    @inlineCallbacks
    def test_inbound_resume_and_reply_with_resume(self):
        yield self.get_transport()
        ussd_string = "xxxx"

        user_content = "Well, what is it you want?"
        inbound_xml = self.make_inbound_xml_string(
            shortcode=ussd_string, msg=user_content)

        d = self.tx_helper.mk_request(_data=inbound_xml, _method='POST')
        [msg] = yield self.tx_helper.wait_for_dispatched_inbound(1)
        self.assert_inbound_message(
            msg,
            session_event=TransportUserMessage.SESSION_RESUME,
            content=user_content,
            to_addr=ussd_string
        )

        reply_content = "We want ... a shrubbery!"
        reply = msg.reply(reply_content, continue_session=True)
        self.tx_helper.dispatch_outbound(reply)
        response = yield d

        self.assert_outbound_message(
            response.delivered_body,
            None,  # appid
            'test_config_app_id',  # config app id
            msg['transport_metadata']['sessionid'],
            reply_content,
            msg['from_addr'],  # msisdn
            continue_session=True,
        )

        [ack] = yield self.tx_helper.wait_for_dispatched_events(1)
        self.assert_ack(ack, reply)

    @inlineCallbacks
    def test_request_with_missing_parameters(self):
        yield self.get_transport()

        inbound_xml = self.make_inbound_xml_string(
            msisdn=None, shortcode=None)

        d = self.tx_helper.mk_request(_data=inbound_xml, _method='POST')
        response = yield d

        self.assertEqual(
            json.loads(response.delivered_body),
            {'missing_parameter': ['msisdn', 'shortcode']})

        self.assertEqual(response.code, 400)

    @inlineCallbacks
    def test_request_with_unexpected_parameters(self):
        yield self.get_transport()

        inbound_xml = self.make_inbound_xml_string()
        bogus_xml = inbound_xml.replace(
            '<appid />', '<unexp_p1>blah</unexp_p1><unexp_p2>blah</unexp_p2>')

        d = self.tx_helper.mk_request(_data=bogus_xml, _method='POST')
        response = yield d

        self.assertEqual(response.code, 400)
        body = json.loads(response.delivered_body)
        self.assertEqual(set(['unexpected_parameter']), set(body.keys()))
        self.assertEqual(
            sorted(body['unexpected_parameter']),
            ['unexp_p1', 'unexp_p2'])

    @inlineCallbacks
    def test_no_reply_to_in_response(self):
        yield self.get_transport()
        msg = yield self.tx_helper.make_dispatch_outbound(
            transport_metadata={
                'sessionid': 'test_sessionid',
                'appid': 'test_appid'
            },
            content="Nudge, nudge, wink, wink. Know what I mean?",
            message_id=1
        )
        [nack] = yield self.tx_helper.wait_for_dispatched_events(1)
        self.assert_nack(nack, msg, "Outbound message is not a reply")

    @inlineCallbacks
    def test_no_content_in_reply(self):
        yield self.get_transport()
        msg = yield self.tx_helper.make_dispatch_outbound(
            transport_metadata={
                'sessionid': 'test_sessionid',
                'appid': 'test_appid'
            },
            content="",
            message_id=1
        )
        [nack] = yield self.tx_helper.wait_for_dispatched_events(1)
        self.assert_nack(nack, msg, "Outbound message has no content.")

    @inlineCallbacks
    def test_failed_request(self):
        yield self.get_transport()
        msg = yield self.tx_helper.make_dispatch_outbound(
            transport_metadata={
                'sessionid': 'test_sessionid',
                'appid': 'test_appid'
            },
            in_reply_to='xxxx',
            content="She turned me into a newt!",
            message_id=1
        )
        [nack] = yield self.tx_helper.wait_for_dispatched_events(1)
        self.assert_nack(nack, msg, "Response to http request failed.")

    @inlineCallbacks
    def test_metadata_handled(self):
        yield self.get_transport()

        ussd_appid = 'xxxx'
        shortcode = "*code#"

        inbound_xml = self.make_inbound_xml_string(
            shortcode=shortcode, appid=ussd_appid, type='1')

        d = self.tx_helper.mk_request(_data=inbound_xml, _method='POST')

        [msg] = yield self.tx_helper.wait_for_dispatched_inbound(1)
        self.assert_inbound_message(
            msg,
            session_event=TransportUserMessage.SESSION_NEW,
            content=None,
            transport_metadata={
                'appid': 'xxxx',
                'sessionid': 'test_session_id',
            }
        )

        reply_content = "We want ... a shrubbery!"
        reply = msg.reply(reply_content, continue_session=True)
        self.tx_helper.dispatch_outbound(reply)
        yield d

        [ack] = yield self.tx_helper.wait_for_dispatched_events(1)
        self.assert_ack(ack, reply)

    @inlineCallbacks
    def test_outbound_unicode(self):
        yield self.get_transport()
        content = "One, two, ... five!"
        ussd_string = '*1234#'

        inbound_xml = self.make_inbound_xml_string(
            shortcode=ussd_string, msg=content)

        d = self.tx_helper.mk_request(_data=inbound_xml, _method='POST')

        [msg] = yield self.tx_helper.wait_for_dispatched_inbound(1)

        reply_content = "Thrëë, my lord."
        reply = msg.reply(reply_content, continue_session=True)
        self.tx_helper.dispatch_outbound(reply)
        response = yield d

        self.assert_outbound_message(
            response.delivered_body,
            None,  # appid
            'test_config_app_id',  # config app id
            msg['transport_metadata']['sessionid'],
            reply_content,
            msg['from_addr'],  # msisdn
            continue_session=True,
        )

        [ack] = yield self.tx_helper.wait_for_dispatched_events(1)
        self.assert_ack(ack, reply)
