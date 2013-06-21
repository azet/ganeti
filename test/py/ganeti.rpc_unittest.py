#!/usr/bin/python
#

# Copyright (C) 2010, 2011, 2012, 2013 Google Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301, USA.


"""Script for testing ganeti.rpc"""

import os
import sys
import unittest
import random
import tempfile

from ganeti import constants
from ganeti import compat
from ganeti import rpc
from ganeti import rpc_defs
from ganeti import http
from ganeti import errors
from ganeti import serializer
from ganeti import objects
from ganeti import backend

import testutils
import mocks


class _FakeRequestProcessor:
  def __init__(self, response_fn):
    self._response_fn = response_fn
    self.reqcount = 0

  def __call__(self, reqs, lock_monitor_cb=None):
    assert lock_monitor_cb is None or callable(lock_monitor_cb)
    for req in reqs:
      self.reqcount += 1
      self._response_fn(req)


def GetFakeSimpleStoreClass(fn):
  class FakeSimpleStore:
    GetNodePrimaryIPList = fn
    GetPrimaryIPFamily = lambda _: None

  return FakeSimpleStore


def _RaiseNotImplemented():
  """Simple wrapper to raise NotImplementedError.

  """
  raise NotImplementedError


class TestRpcProcessor(unittest.TestCase):
  def _FakeAddressLookup(self, map):
    return lambda node_list: [map.get(node) for node in node_list]

  def _GetVersionResponse(self, req):
    self.assertEqual(req.host, "127.0.0.1")
    self.assertEqual(req.port, 24094)
    self.assertEqual(req.path, "/version")
    self.assertEqual(req.read_timeout, constants.RPC_TMO_URGENT)
    req.success = True
    req.resp_status_code = http.HTTP_OK
    req.resp_body = serializer.DumpJson((True, 123))

  def testVersionSuccess(self):
    resolver = rpc._StaticResolver(["127.0.0.1"])
    http_proc = _FakeRequestProcessor(self._GetVersionResponse)
    proc = rpc._RpcProcessor(resolver, 24094)
    result = proc(["localhost"], "version", {"localhost": ""}, 60,
                  NotImplemented, _req_process_fn=http_proc)
    self.assertEqual(result.keys(), ["localhost"])
    lhresp = result["localhost"]
    self.assertFalse(lhresp.offline)
    self.assertEqual(lhresp.node, "localhost")
    self.assertFalse(lhresp.fail_msg)
    self.assertEqual(lhresp.payload, 123)
    self.assertEqual(lhresp.call, "version")
    lhresp.Raise("should not raise")
    self.assertEqual(http_proc.reqcount, 1)

  def _ReadTimeoutResponse(self, req):
    self.assertEqual(req.host, "192.0.2.13")
    self.assertEqual(req.port, 19176)
    self.assertEqual(req.path, "/version")
    self.assertEqual(req.read_timeout, 12356)
    req.success = True
    req.resp_status_code = http.HTTP_OK
    req.resp_body = serializer.DumpJson((True, -1))

  def testReadTimeout(self):
    resolver = rpc._StaticResolver(["192.0.2.13"])
    http_proc = _FakeRequestProcessor(self._ReadTimeoutResponse)
    proc = rpc._RpcProcessor(resolver, 19176)
    host = "node31856"
    body = {host: ""}
    result = proc([host], "version", body, 12356, NotImplemented,
                  _req_process_fn=http_proc)
    self.assertEqual(result.keys(), [host])
    lhresp = result[host]
    self.assertFalse(lhresp.offline)
    self.assertEqual(lhresp.node, host)
    self.assertFalse(lhresp.fail_msg)
    self.assertEqual(lhresp.payload, -1)
    self.assertEqual(lhresp.call, "version")
    lhresp.Raise("should not raise")
    self.assertEqual(http_proc.reqcount, 1)

  def testOfflineNode(self):
    resolver = rpc._StaticResolver([rpc._OFFLINE])
    http_proc = _FakeRequestProcessor(NotImplemented)
    proc = rpc._RpcProcessor(resolver, 30668)
    host = "n17296"
    body = {host: ""}
    result = proc([host], "version", body, 60, NotImplemented,
                  _req_process_fn=http_proc)
    self.assertEqual(result.keys(), [host])
    lhresp = result[host]
    self.assertTrue(lhresp.offline)
    self.assertEqual(lhresp.node, host)
    self.assertTrue(lhresp.fail_msg)
    self.assertFalse(lhresp.payload)
    self.assertEqual(lhresp.call, "version")

    # With a message
    self.assertRaises(errors.OpExecError, lhresp.Raise, "should raise")

    # No message
    self.assertRaises(errors.OpExecError, lhresp.Raise, None)

    self.assertEqual(http_proc.reqcount, 0)

  def _GetMultiVersionResponse(self, req):
    self.assert_(req.host.startswith("node"))
    self.assertEqual(req.port, 23245)
    self.assertEqual(req.path, "/version")
    req.success = True
    req.resp_status_code = http.HTTP_OK
    req.resp_body = serializer.DumpJson((True, 987))

  def testMultiVersionSuccess(self):
    nodes = ["node%s" % i for i in range(50)]
    body = dict((n, "") for n in nodes)
    resolver = rpc._StaticResolver(nodes)
    http_proc = _FakeRequestProcessor(self._GetMultiVersionResponse)
    proc = rpc._RpcProcessor(resolver, 23245)
    result = proc(nodes, "version", body, 60, NotImplemented,
                  _req_process_fn=http_proc)
    self.assertEqual(sorted(result.keys()), sorted(nodes))

    for name in nodes:
      lhresp = result[name]
      self.assertFalse(lhresp.offline)
      self.assertEqual(lhresp.node, name)
      self.assertFalse(lhresp.fail_msg)
      self.assertEqual(lhresp.payload, 987)
      self.assertEqual(lhresp.call, "version")
      lhresp.Raise("should not raise")

    self.assertEqual(http_proc.reqcount, len(nodes))

  def _GetVersionResponseFail(self, errinfo, req):
    self.assertEqual(req.path, "/version")
    req.success = True
    req.resp_status_code = http.HTTP_OK
    req.resp_body = serializer.DumpJson((False, errinfo))

  def testVersionFailure(self):
    resolver = rpc._StaticResolver(["aef9ur4i.example.com"])
    proc = rpc._RpcProcessor(resolver, 5903)
    for errinfo in [None, "Unknown error"]:
      http_proc = \
        _FakeRequestProcessor(compat.partial(self._GetVersionResponseFail,
                                             errinfo))
      host = "aef9ur4i.example.com"
      body = {host: ""}
      result = proc(body.keys(), "version", body, 60, NotImplemented,
                    _req_process_fn=http_proc)
      self.assertEqual(result.keys(), [host])
      lhresp = result[host]
      self.assertFalse(lhresp.offline)
      self.assertEqual(lhresp.node, host)
      self.assert_(lhresp.fail_msg)
      self.assertFalse(lhresp.payload)
      self.assertEqual(lhresp.call, "version")
      self.assertRaises(errors.OpExecError, lhresp.Raise, "failed")
      self.assertEqual(http_proc.reqcount, 1)

  def _GetHttpErrorResponse(self, httperrnodes, failnodes, req):
    self.assertEqual(req.path, "/vg_list")
    self.assertEqual(req.port, 15165)

    if req.host in httperrnodes:
      req.success = False
      req.error = "Node set up for HTTP errors"

    elif req.host in failnodes:
      req.success = True
      req.resp_status_code = 404
      req.resp_body = serializer.DumpJson({
        "code": 404,
        "message": "Method not found",
        "explain": "Explanation goes here",
        })
    else:
      req.success = True
      req.resp_status_code = http.HTTP_OK
      req.resp_body = serializer.DumpJson((True, hash(req.host)))

  def testHttpError(self):
    nodes = ["uaf6pbbv%s" % i for i in range(50)]
    body = dict((n, "") for n in nodes)
    resolver = rpc._StaticResolver(nodes)

    httperrnodes = set(nodes[1::7])
    self.assertEqual(len(httperrnodes), 7)

    failnodes = set(nodes[2::3]) - httperrnodes
    self.assertEqual(len(failnodes), 14)

    self.assertEqual(len(set(nodes) - failnodes - httperrnodes), 29)

    proc = rpc._RpcProcessor(resolver, 15165)
    http_proc = \
      _FakeRequestProcessor(compat.partial(self._GetHttpErrorResponse,
                                           httperrnodes, failnodes))
    result = proc(nodes, "vg_list", body,
                  constants.RPC_TMO_URGENT, NotImplemented,
                  _req_process_fn=http_proc)
    self.assertEqual(sorted(result.keys()), sorted(nodes))

    for name in nodes:
      lhresp = result[name]
      self.assertFalse(lhresp.offline)
      self.assertEqual(lhresp.node, name)
      self.assertEqual(lhresp.call, "vg_list")

      if name in httperrnodes:
        self.assert_(lhresp.fail_msg)
        self.assertRaises(errors.OpExecError, lhresp.Raise, "failed")
      elif name in failnodes:
        self.assert_(lhresp.fail_msg)
        self.assertRaises(errors.OpPrereqError, lhresp.Raise, "failed",
                          prereq=True, ecode=errors.ECODE_INVAL)
      else:
        self.assertFalse(lhresp.fail_msg)
        self.assertEqual(lhresp.payload, hash(name))
        lhresp.Raise("should not raise")

    self.assertEqual(http_proc.reqcount, len(nodes))

  def _GetInvalidResponseA(self, req):
    self.assertEqual(req.path, "/version")
    req.success = True
    req.resp_status_code = http.HTTP_OK
    req.resp_body = serializer.DumpJson(("This", "is", "an", "invalid",
                                         "response", "!", 1, 2, 3))

  def _GetInvalidResponseB(self, req):
    self.assertEqual(req.path, "/version")
    req.success = True
    req.resp_status_code = http.HTTP_OK
    req.resp_body = serializer.DumpJson("invalid response")

  def testInvalidResponse(self):
    resolver = rpc._StaticResolver(["oqo7lanhly.example.com"])
    proc = rpc._RpcProcessor(resolver, 19978)

    for fn in [self._GetInvalidResponseA, self._GetInvalidResponseB]:
      http_proc = _FakeRequestProcessor(fn)
      host = "oqo7lanhly.example.com"
      body = {host: ""}
      result = proc([host], "version", body, 60, NotImplemented,
                    _req_process_fn=http_proc)
      self.assertEqual(result.keys(), [host])
      lhresp = result[host]
      self.assertFalse(lhresp.offline)
      self.assertEqual(lhresp.node, host)
      self.assert_(lhresp.fail_msg)
      self.assertFalse(lhresp.payload)
      self.assertEqual(lhresp.call, "version")
      self.assertRaises(errors.OpExecError, lhresp.Raise, "failed")
      self.assertEqual(http_proc.reqcount, 1)

  def _GetBodyTestResponse(self, test_data, req):
    self.assertEqual(req.host, "192.0.2.84")
    self.assertEqual(req.port, 18700)
    self.assertEqual(req.path, "/upload_file")
    self.assertEqual(serializer.LoadJson(req.post_data), test_data)
    req.success = True
    req.resp_status_code = http.HTTP_OK
    req.resp_body = serializer.DumpJson((True, None))

  def testResponseBody(self):
    test_data = {
      "Hello": "World",
      "xyz": range(10),
      }
    resolver = rpc._StaticResolver(["192.0.2.84"])
    http_proc = _FakeRequestProcessor(compat.partial(self._GetBodyTestResponse,
                                                     test_data))
    proc = rpc._RpcProcessor(resolver, 18700)
    host = "node19759"
    body = {host: serializer.DumpJson(test_data)}
    result = proc([host], "upload_file", body, 30, NotImplemented,
                  _req_process_fn=http_proc)
    self.assertEqual(result.keys(), [host])
    lhresp = result[host]
    self.assertFalse(lhresp.offline)
    self.assertEqual(lhresp.node, host)
    self.assertFalse(lhresp.fail_msg)
    self.assertEqual(lhresp.payload, None)
    self.assertEqual(lhresp.call, "upload_file")
    lhresp.Raise("should not raise")
    self.assertEqual(http_proc.reqcount, 1)


class TestSsconfResolver(unittest.TestCase):
  def testSsconfLookup(self):
    addr_list = ["192.0.2.%d" % n for n in range(0, 255, 13)]
    node_list = ["node%d.example.com" % n for n in range(0, 255, 13)]
    node_addr_list = [" ".join(t) for t in zip(node_list, addr_list)]
    ssc = GetFakeSimpleStoreClass(lambda _: node_addr_list)
    result = rpc._SsconfResolver(True, node_list, NotImplemented,
                                 ssc=ssc, nslookup_fn=NotImplemented)
    self.assertEqual(result, zip(node_list, addr_list, node_list))

  def testNsLookup(self):
    addr_list = ["192.0.2.%d" % n for n in range(0, 255, 13)]
    node_list = ["node%d.example.com" % n for n in range(0, 255, 13)]
    ssc = GetFakeSimpleStoreClass(lambda _: [])
    node_addr_map = dict(zip(node_list, addr_list))
    nslookup_fn = lambda name, family=None: node_addr_map.get(name)
    result = rpc._SsconfResolver(True, node_list, NotImplemented,
                                 ssc=ssc, nslookup_fn=nslookup_fn)
    self.assertEqual(result, zip(node_list, addr_list, node_list))

  def testDisabledSsconfIp(self):
    addr_list = ["192.0.2.%d" % n for n in range(0, 255, 13)]
    node_list = ["node%d.example.com" % n for n in range(0, 255, 13)]
    ssc = GetFakeSimpleStoreClass(_RaiseNotImplemented)
    node_addr_map = dict(zip(node_list, addr_list))
    nslookup_fn = lambda name, family=None: node_addr_map.get(name)
    result = rpc._SsconfResolver(False, node_list, NotImplemented,
                                 ssc=ssc, nslookup_fn=nslookup_fn)
    self.assertEqual(result, zip(node_list, addr_list, node_list))

  def testBothLookups(self):
    addr_list = ["192.0.2.%d" % n for n in range(0, 255, 13)]
    node_list = ["node%d.example.com" % n for n in range(0, 255, 13)]
    n = len(addr_list) / 2
    node_addr_list = [" ".join(t) for t in zip(node_list[n:], addr_list[n:])]
    ssc = GetFakeSimpleStoreClass(lambda _: node_addr_list)
    node_addr_map = dict(zip(node_list[:n], addr_list[:n]))
    nslookup_fn = lambda name, family=None: node_addr_map.get(name)
    result = rpc._SsconfResolver(True, node_list, NotImplemented,
                                 ssc=ssc, nslookup_fn=nslookup_fn)
    self.assertEqual(result, zip(node_list, addr_list, node_list))

  def testAddressLookupIPv6(self):
    addr_list = ["2001:db8::%d" % n for n in range(0, 255, 11)]
    node_list = ["node%d.example.com" % n for n in range(0, 255, 11)]
    node_addr_list = [" ".join(t) for t in zip(node_list, addr_list)]
    ssc = GetFakeSimpleStoreClass(lambda _: node_addr_list)
    result = rpc._SsconfResolver(True, node_list, NotImplemented,
                                 ssc=ssc, nslookup_fn=NotImplemented)
    self.assertEqual(result, zip(node_list, addr_list, node_list))


class TestStaticResolver(unittest.TestCase):
  def test(self):
    addresses = ["192.0.2.%d" % n for n in range(0, 123, 7)]
    nodes = ["node%s.example.com" % n for n in range(0, 123, 7)]
    res = rpc._StaticResolver(addresses)
    self.assertEqual(res(nodes, NotImplemented), zip(nodes, addresses, nodes))

  def testWrongLength(self):
    res = rpc._StaticResolver([])
    self.assertRaises(AssertionError, res, ["abc"], NotImplemented)


class TestNodeConfigResolver(unittest.TestCase):
  @staticmethod
  def _GetSingleOnlineNode(uuid):
    assert uuid == "node90-uuid"
    return objects.Node(name="node90.example.com",
                        uuid=uuid,
                        offline=False,
                        primary_ip="192.0.2.90")

  @staticmethod
  def _GetSingleOfflineNode(uuid):
    assert uuid == "node100-uuid"
    return objects.Node(name="node100.example.com",
                        uuid=uuid,
                        offline=True,
                        primary_ip="192.0.2.100")

  def testSingleOnline(self):
    self.assertEqual(rpc._NodeConfigResolver(self._GetSingleOnlineNode,
                                             NotImplemented,
                                             ["node90-uuid"], None),
                     [("node90.example.com", "192.0.2.90", "node90-uuid")])

  def testSingleOffline(self):
    self.assertEqual(rpc._NodeConfigResolver(self._GetSingleOfflineNode,
                                             NotImplemented,
                                             ["node100-uuid"], None),
                     [("node100.example.com", rpc._OFFLINE, "node100-uuid")])

  def testSingleOfflineWithAcceptOffline(self):
    fn = self._GetSingleOfflineNode
    assert fn("node100-uuid").offline
    self.assertEqual(rpc._NodeConfigResolver(fn, NotImplemented,
                                             ["node100-uuid"],
                                             rpc_defs.ACCEPT_OFFLINE_NODE),
                     [("node100.example.com", "192.0.2.100", "node100-uuid")])
    for i in [False, True, "", "Hello", 0, 1]:
      self.assertRaises(AssertionError, rpc._NodeConfigResolver,
                        fn, NotImplemented, ["node100.example.com"], i)

  def testUnknownSingleNode(self):
    self.assertEqual(rpc._NodeConfigResolver(lambda _: None, NotImplemented,
                                             ["node110.example.com"], None),
                     [("node110.example.com", "node110.example.com",
                       "node110.example.com")])

  def testMultiEmpty(self):
    self.assertEqual(rpc._NodeConfigResolver(NotImplemented,
                                             lambda: {},
                                             [], None),
                     [])

  def testMultiSomeOffline(self):
    nodes = dict(("node%s-uuid" % i,
                  objects.Node(name="node%s.example.com" % i,
                               offline=((i % 3) == 0),
                               primary_ip="192.0.2.%s" % i,
                               uuid="node%s-uuid" % i))
                  for i in range(1, 255))

    # Resolve no names
    self.assertEqual(rpc._NodeConfigResolver(NotImplemented,
                                             lambda: nodes,
                                             [], None),
                     [])

    # Offline, online and unknown hosts
    self.assertEqual(rpc._NodeConfigResolver(NotImplemented,
                                             lambda: nodes,
                                             ["node3-uuid",
                                              "node92-uuid",
                                              "node54-uuid",
                                              "unknown.example.com",],
                                             None), [
      ("node3.example.com", rpc._OFFLINE, "node3-uuid"),
      ("node92.example.com", "192.0.2.92", "node92-uuid"),
      ("node54.example.com", rpc._OFFLINE, "node54-uuid"),
      ("unknown.example.com", "unknown.example.com", "unknown.example.com"),
      ])


class TestCompress(unittest.TestCase):
  def test(self):
    for data in ["", "Hello", "Hello World!\nnew\nlines"]:
      self.assertEqual(rpc._Compress(data),
                       (constants.RPC_ENCODING_NONE, data))

    for data in [512 * " ", 5242 * "Hello World!\n"]:
      compressed = rpc._Compress(data)
      self.assertEqual(len(compressed), 2)
      self.assertEqual(backend._Decompress(compressed), data)

  def testDecompression(self):
    self.assertRaises(AssertionError, backend._Decompress, "")
    self.assertRaises(AssertionError, backend._Decompress, [""])
    self.assertRaises(AssertionError, backend._Decompress,
                      ("unknown compression", "data"))
    self.assertRaises(Exception, backend._Decompress,
                      (constants.RPC_ENCODING_ZLIB_BASE64, "invalid zlib data"))


class TestRpcClientBase(unittest.TestCase):
  def testNoHosts(self):
    cdef = ("test_call", NotImplemented, None, constants.RPC_TMO_SLOW, [],
            None, None, NotImplemented)
    http_proc = _FakeRequestProcessor(NotImplemented)
    client = rpc._RpcClientBase(rpc._StaticResolver([]), NotImplemented,
                                _req_process_fn=http_proc)
    self.assertEqual(client._Call(cdef, [], []), {})

    # Test wrong number of arguments
    self.assertRaises(errors.ProgrammerError, client._Call,
                      cdef, [], [0, 1, 2])

  def testTimeout(self):
    def _CalcTimeout((arg1, arg2)):
      return arg1 + arg2

    def _VerifyRequest(exp_timeout, req):
      self.assertEqual(req.read_timeout, exp_timeout)

      req.success = True
      req.resp_status_code = http.HTTP_OK
      req.resp_body = serializer.DumpJson((True, hex(req.read_timeout)))

    resolver = rpc._StaticResolver([
      "192.0.2.1",
      "192.0.2.2",
      ])

    nodes = [
      "node1.example.com",
      "node2.example.com",
      ]

    tests = [(100, None, 100), (30, None, 30)]
    tests.extend((_CalcTimeout, i, i + 300)
                 for i in [0, 5, 16485, 30516])

    for timeout, arg1, exp_timeout in tests:
      cdef = ("test_call", NotImplemented, None, timeout, [
        ("arg1", None, NotImplemented),
        ("arg2", None, NotImplemented),
        ], None, None, NotImplemented)

      http_proc = _FakeRequestProcessor(compat.partial(_VerifyRequest,
                                                       exp_timeout))
      client = rpc._RpcClientBase(resolver, NotImplemented,
                                  _req_process_fn=http_proc)
      result = client._Call(cdef, nodes, [arg1, 300])
      self.assertEqual(len(result), len(nodes))
      self.assertTrue(compat.all(not res.fail_msg and
                                 res.payload == hex(exp_timeout)
                                 for res in result.values()))

  def testArgumentEncoder(self):
    (AT1, AT2) = range(1, 3)

    resolver = rpc._StaticResolver([
      "192.0.2.5",
      "192.0.2.6",
      ])

    nodes = [
      "node5.example.com",
      "node6.example.com",
      ]

    encoders = {
      AT1: hex,
      AT2: hash,
      }

    cdef = ("test_call", NotImplemented, None, constants.RPC_TMO_NORMAL, [
      ("arg0", None, NotImplemented),
      ("arg1", AT1, NotImplemented),
      ("arg1", AT2, NotImplemented),
      ], None, None, NotImplemented)

    def _VerifyRequest(req):
      req.success = True
      req.resp_status_code = http.HTTP_OK
      req.resp_body = serializer.DumpJson((True, req.post_data))

    http_proc = _FakeRequestProcessor(_VerifyRequest)

    for num in [0, 3796, 9032119]:
      client = rpc._RpcClientBase(resolver, encoders.get,
                                  _req_process_fn=http_proc)
      result = client._Call(cdef, nodes, ["foo", num, "Hello%s" % num])
      self.assertEqual(len(result), len(nodes))
      for res in result.values():
        self.assertFalse(res.fail_msg)
        self.assertEqual(serializer.LoadJson(res.payload),
                         ["foo", hex(num), hash("Hello%s" % num)])

  def testPostProc(self):
    def _VerifyRequest(nums, req):
      req.success = True
      req.resp_status_code = http.HTTP_OK
      req.resp_body = serializer.DumpJson((True, nums))

    resolver = rpc._StaticResolver([
      "192.0.2.90",
      "192.0.2.95",
      ])

    nodes = [
      "node90.example.com",
      "node95.example.com",
      ]

    def _PostProc(res):
      self.assertFalse(res.fail_msg)
      res.payload = sum(res.payload)
      return res

    cdef = ("test_call", NotImplemented, None, constants.RPC_TMO_NORMAL, [],
            None, _PostProc, NotImplemented)

    # Seeded random generator
    rnd = random.Random(20299)

    for i in [0, 4, 74, 1391]:
      nums = [rnd.randint(0, 1000) for _ in range(i)]
      http_proc = _FakeRequestProcessor(compat.partial(_VerifyRequest, nums))
      client = rpc._RpcClientBase(resolver, NotImplemented,
                                  _req_process_fn=http_proc)
      result = client._Call(cdef, nodes, [])
      self.assertEqual(len(result), len(nodes))
      for res in result.values():
        self.assertFalse(res.fail_msg)
        self.assertEqual(res.payload, sum(nums))

  def testPreProc(self):
    def _VerifyRequest(req):
      req.success = True
      req.resp_status_code = http.HTTP_OK
      req.resp_body = serializer.DumpJson((True, req.post_data))

    resolver = rpc._StaticResolver([
      "192.0.2.30",
      "192.0.2.35",
      ])

    nodes = [
      "node30.example.com",
      "node35.example.com",
      ]

    def _PreProc(node, data):
      self.assertEqual(len(data), 1)
      return data[0] + node

    cdef = ("test_call", NotImplemented, None, constants.RPC_TMO_NORMAL, [
      ("arg0", None, NotImplemented),
      ], _PreProc, None, NotImplemented)

    http_proc = _FakeRequestProcessor(_VerifyRequest)
    client = rpc._RpcClientBase(resolver, NotImplemented,
                                _req_process_fn=http_proc)

    for prefix in ["foo", "bar", "baz"]:
      result = client._Call(cdef, nodes, [prefix])
      self.assertEqual(len(result), len(nodes))
      for (idx, (node, res)) in enumerate(result.items()):
        self.assertFalse(res.fail_msg)
        self.assertEqual(serializer.LoadJson(res.payload), prefix + node)

  def testResolverOptions(self):
    def _VerifyRequest(req):
      req.success = True
      req.resp_status_code = http.HTTP_OK
      req.resp_body = serializer.DumpJson((True, req.post_data))

    nodes = [
      "node30.example.com",
      "node35.example.com",
      ]

    def _Resolver(expected, hosts, options):
      self.assertEqual(hosts, nodes)
      self.assertEqual(options, expected)
      return zip(hosts, nodes, hosts)

    def _DynamicResolverOptions((arg0, )):
      return sum(arg0)

    tests = [
      (None, None, None),
      (rpc_defs.ACCEPT_OFFLINE_NODE, None, rpc_defs.ACCEPT_OFFLINE_NODE),
      (False, None, False),
      (True, None, True),
      (0, None, 0),
      (_DynamicResolverOptions, [1, 2, 3], 6),
      (_DynamicResolverOptions, range(4, 19), 165),
      ]

    for (resolver_opts, arg0, expected) in tests:
      cdef = ("test_call", NotImplemented, resolver_opts,
              constants.RPC_TMO_NORMAL, [
        ("arg0", None, NotImplemented),
        ], None, None, NotImplemented)

      http_proc = _FakeRequestProcessor(_VerifyRequest)

      client = rpc._RpcClientBase(compat.partial(_Resolver, expected),
                                  NotImplemented, _req_process_fn=http_proc)
      result = client._Call(cdef, nodes, [arg0])
      self.assertEqual(len(result), len(nodes))
      for (idx, (node, res)) in enumerate(result.items()):
        self.assertFalse(res.fail_msg)


class _FakeConfigForRpcRunner:
  GetAllNodesInfo = NotImplemented

  def __init__(self, cluster=NotImplemented):
    self._cluster = cluster

  def GetNodeInfo(self, name):
    return objects.Node(name=name)

  def GetClusterInfo(self):
    return self._cluster

  def GetInstanceDiskParams(self, _):
    return constants.DISK_DT_DEFAULTS


class TestRpcRunner(unittest.TestCase):
  def testUploadFile(self):
    data = 1779 * "Hello World\n"

    tmpfile = tempfile.NamedTemporaryFile()
    tmpfile.write(data)
    tmpfile.flush()
    st = os.stat(tmpfile.name)

    def _VerifyRequest(req):
      (uldata, ) = serializer.LoadJson(req.post_data)
      self.assertEqual(len(uldata), 7)
      self.assertEqual(uldata[0], tmpfile.name)
      self.assertEqual(list(uldata[1]), list(rpc._Compress(data)))
      self.assertEqual(uldata[2], st.st_mode)
      self.assertEqual(uldata[3], "user%s" % os.getuid())
      self.assertEqual(uldata[4], "group%s" % os.getgid())
      self.assertTrue(uldata[5] is not None)
      self.assertEqual(uldata[6], st.st_mtime)

      req.success = True
      req.resp_status_code = http.HTTP_OK
      req.resp_body = serializer.DumpJson((True, None))

    http_proc = _FakeRequestProcessor(_VerifyRequest)

    std_runner = rpc.RpcRunner(_FakeConfigForRpcRunner(), None,
                               _req_process_fn=http_proc,
                               _getents=mocks.FakeGetentResolver)

    cfg_runner = rpc.ConfigRunner(None, ["192.0.2.13"],
                                  _req_process_fn=http_proc,
                                  _getents=mocks.FakeGetentResolver)

    nodes = [
      "node1.example.com",
      ]

    for runner in [std_runner, cfg_runner]:
      result = runner.call_upload_file(nodes, tmpfile.name)
      self.assertEqual(len(result), len(nodes))
      for (idx, (node, res)) in enumerate(result.items()):
        self.assertFalse(res.fail_msg)

  def testEncodeInstance(self):
    cluster = objects.Cluster(hvparams={
      constants.HT_KVM: {
        constants.HV_BLOCKDEV_PREFIX: "foo",
        },
      },
      beparams={
        constants.PP_DEFAULT: {
          constants.BE_MAXMEM: 8192,
          },
        },
      os_hvp={},
      osparams={
        "linux": {
          "role": "unknown",
          },
        })
    cluster.UpgradeConfig()

    inst = objects.Instance(name="inst1.example.com",
      hypervisor=constants.HT_FAKE,
      os="linux",
      hvparams={
        constants.HT_KVM: {
          constants.HV_BLOCKDEV_PREFIX: "bar",
          constants.HV_ROOT_PATH: "/tmp",
          },
        },
      beparams={
        constants.BE_MINMEM: 128,
        constants.BE_MAXMEM: 256,
        },
      nics=[
        objects.NIC(nicparams={
          constants.NIC_MODE: "mymode",
          }),
        ],
      disk_template=constants.DT_PLAIN,
      disks=[
        objects.Disk(dev_type=constants.LD_LV, size=4096,
                     logical_id=("vg", "disk6120")),
        objects.Disk(dev_type=constants.LD_LV, size=1024,
                     logical_id=("vg", "disk8508")),
        ])
    inst.UpgradeConfig()

    cfg = _FakeConfigForRpcRunner(cluster=cluster)
    runner = rpc.RpcRunner(cfg, None,
                           _req_process_fn=NotImplemented,
                           _getents=mocks.FakeGetentResolver)

    def _CheckBasics(result):
      self.assertEqual(result["name"], "inst1.example.com")
      self.assertEqual(result["os"], "linux")
      self.assertEqual(result["beparams"][constants.BE_MINMEM], 128)
      self.assertEqual(len(result["hvparams"]), 1)
      self.assertEqual(len(result["nics"]), 1)
      self.assertEqual(result["nics"][0]["nicparams"][constants.NIC_MODE],
                       "mymode")

    # Generic object serialization
    result = runner._encoder((rpc_defs.ED_OBJECT_DICT, inst))
    _CheckBasics(result)

    result = runner._encoder((rpc_defs.ED_OBJECT_DICT_LIST, 5 * [inst]))
    map(_CheckBasics, result)

    # Just an instance
    result = runner._encoder((rpc_defs.ED_INST_DICT, inst))
    _CheckBasics(result)
    self.assertEqual(result["beparams"][constants.BE_MAXMEM], 256)
    self.assertEqual(result["hvparams"][constants.HT_KVM], {
      constants.HV_BLOCKDEV_PREFIX: "bar",
      constants.HV_ROOT_PATH: "/tmp",
      })
    self.assertEqual(result["osparams"], {
      "role": "unknown",
      })

    # Instance with OS parameters
    result = runner._encoder((rpc_defs.ED_INST_DICT_OSP_DP, (inst, {
      "role": "webserver",
      "other": "field",
      })))
    _CheckBasics(result)
    self.assertEqual(result["beparams"][constants.BE_MAXMEM], 256)
    self.assertEqual(result["hvparams"][constants.HT_KVM], {
      constants.HV_BLOCKDEV_PREFIX: "bar",
      constants.HV_ROOT_PATH: "/tmp",
      })
    self.assertEqual(result["osparams"], {
      "role": "webserver",
      "other": "field",
      })

    # Instance with hypervisor and backend parameters
    result = runner._encoder((rpc_defs.ED_INST_DICT_HVP_BEP_DP, (inst, {
      constants.HT_KVM: {
        constants.HV_BOOT_ORDER: "xyz",
        },
      }, {
      constants.BE_VCPUS: 100,
      constants.BE_MAXMEM: 4096,
      })))
    _CheckBasics(result)
    self.assertEqual(result["beparams"][constants.BE_MAXMEM], 4096)
    self.assertEqual(result["beparams"][constants.BE_VCPUS], 100)
    self.assertEqual(result["hvparams"][constants.HT_KVM], {
      constants.HV_BOOT_ORDER: "xyz",
      })
    self.assertEqual(result["disks"], [{
      "dev_type": constants.LD_LV,
      "size": 4096,
      "logical_id": ("vg", "disk6120"),
      "params": constants.DISK_DT_DEFAULTS[inst.disk_template],
      }, {
      "dev_type": constants.LD_LV,
      "size": 1024,
      "logical_id": ("vg", "disk8508"),
      "params": constants.DISK_DT_DEFAULTS[inst.disk_template],
      }])

    self.assertTrue(compat.all(disk.params == {} for disk in inst.disks),
                    msg="Configuration objects were modified")


class TestLegacyNodeInfo(unittest.TestCase):
  KEY_BOOT = "bootid"
  KEY_VG0 = "name"
  KEY_VG1 = "storage_free"
  KEY_VG2 = "storage_size"
  KEY_HV = "cpu_count"
  KEY_SP1 = "spindles_free"
  KEY_SP2 = "spindles_total"
  KEY_ST = "type" # key for storage type
  VAL_BOOT = 0
  VAL_VG0 = "xy"
  VAL_VG1 = 11
  VAL_VG2 = 12
  VAL_VG3 = "lvm-vg"
  VAL_HV = 2
  VAL_SP0 = "ab"
  VAL_SP1 = 31
  VAL_SP2 = 32
  VAL_SP3 = "lvm-pv"
  DICT_VG = {
    KEY_VG0: VAL_VG0,
    KEY_VG1: VAL_VG1,
    KEY_VG2: VAL_VG2,
    KEY_ST: VAL_VG3,
    }
  DICT_HV = {KEY_HV: VAL_HV}
  DICT_SP = {
    KEY_ST: VAL_SP3,
    KEY_VG0: VAL_SP0,
    KEY_VG1: VAL_SP1,
    KEY_VG2: VAL_SP2,
    }
  STD_LST = [VAL_BOOT, [DICT_VG, DICT_SP], [DICT_HV]]
  STD_DICT = {
    KEY_BOOT: VAL_BOOT,
    KEY_VG0: VAL_VG0,
    KEY_VG1: VAL_VG1,
    KEY_VG2: VAL_VG2,
    KEY_HV: VAL_HV,
    KEY_SP1: VAL_SP1,
    KEY_SP2: VAL_SP2,
    }

  def testStandard(self):
    result = rpc.MakeLegacyNodeInfo(self.STD_LST)
    self.assertEqual(result, self.STD_DICT)

  def testReqVg(self):
    my_lst = [self.VAL_BOOT, [], [self.DICT_HV]]
    self.assertRaises(errors.OpExecError, rpc.MakeLegacyNodeInfo, my_lst)

  def testNoReqVg(self):
    my_lst = [self.VAL_BOOT, [], [self.DICT_HV]]
    result = rpc.MakeLegacyNodeInfo(my_lst, require_vg_info = False)
    self.assertEqual(result, {self.KEY_BOOT: self.VAL_BOOT,
                              self.KEY_HV: self.VAL_HV})
    result = rpc.MakeLegacyNodeInfo(self.STD_LST, require_vg_info = False)
    self.assertEqual(result, self.STD_DICT)


class TestAddDefaultStorageInfoToLegacyNodeInfo(unittest.TestCase):

  def setUp(self):
    self.free_storage_file = 23
    self.total_storage_file = 42
    self.free_storage_lvm = 69
    self.total_storage_lvm = 666
    self.node_info = [{"name": "mynode",
                       "type": constants.ST_FILE,
                       "storage_free": self.free_storage_file,
                       "storage_size": self.total_storage_file},
                      {"name": "mynode",
                       "type": constants.ST_LVM_VG,
                       "storage_free": self.free_storage_lvm,
                       "storage_size": self.total_storage_lvm},
                      {"name": "mynode",
                       "type": constants.ST_LVM_PV,
                       "storage_free": 33,
                       "storage_size": 44}]

  def testAddDefaultStorageInfoToLegacyNodeInfo(self):
    result = {}
    has_lvm = False
    rpc._AddDefaultStorageInfoToLegacyNodeInfo(result, self.node_info, has_lvm)
    self.assertEqual(self.free_storage_file, result["storage_free"])
    self.assertEqual(self.total_storage_file, result["storage_size"])

  def testAddDefaultStorageInfoToLegacyNodeInfoOverrideDefault(self):
    result = {}
    has_lvm = True
    rpc._AddDefaultStorageInfoToLegacyNodeInfo(result, self.node_info, has_lvm)
    self.assertEqual(self.free_storage_lvm, result["storage_free"])
    self.assertEqual(self.total_storage_lvm, result["storage_size"])

  def testAddDefaultStorageInfoToLegacyNodeInfoNoDefaults(self):
    result = {}
    has_lvm = False
    rpc._AddDefaultStorageInfoToLegacyNodeInfo(result, self.node_info[-1:],
                                               has_lvm)
    self.assertFalse("storage_free" in result)
    self.assertFalse("storage_size" in result)

  def testAddDefaultStorageInfoToLegacyNodeInfoNoLvm(self):
    result = {}
    has_lvm = True
    self.assertRaises(errors.OpExecError,
                      rpc._AddDefaultStorageInfoToLegacyNodeInfo,
                      result, self.node_info[-1:], has_lvm)


if __name__ == "__main__":
  testutils.GanetiTestProgram()
