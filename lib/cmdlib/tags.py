#
#

# Copyright (C) 2006, 2007, 2008, 2009, 2010, 2011, 2012, 2013 Google Inc.
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


"""Logical units dealing with tags."""

import re

from ganeti import constants
from ganeti import errors
from ganeti import locking
from ganeti import objects
from ganeti import utils
from ganeti.cmdlib.base import NoHooksLU
from ganeti.cmdlib.common import ExpandNodeName, ExpandInstanceName, ShareAll


class TagsLU(NoHooksLU): # pylint: disable=W0223
  """Generic tags LU.

  This is an abstract class which is the parent of all the other tags LUs.

  """
  def ExpandNames(self):
    self.group_uuid = None
    self.needed_locks = {}

    if self.op.kind == constants.TAG_NODE:
      self.op.name = ExpandNodeName(self.cfg, self.op.name)
      lock_level = locking.LEVEL_NODE
      lock_name = self.op.name
    elif self.op.kind == constants.TAG_INSTANCE:
      self.op.name = ExpandInstanceName(self.cfg, self.op.name)
      lock_level = locking.LEVEL_INSTANCE
      lock_name = self.op.name
    elif self.op.kind == constants.TAG_NODEGROUP:
      self.group_uuid = self.cfg.LookupNodeGroup(self.op.name)
      lock_level = locking.LEVEL_NODEGROUP
      lock_name = self.group_uuid
    elif self.op.kind == constants.TAG_NETWORK:
      self.network_uuid = self.cfg.LookupNetwork(self.op.name)
      lock_level = locking.LEVEL_NETWORK
      lock_name = self.network_uuid
    else:
      lock_level = None
      lock_name = None

    if lock_level and getattr(self.op, "use_locking", True):
      self.needed_locks[lock_level] = lock_name

    # FIXME: Acquire BGL for cluster tag operations (as of this writing it's
    # not possible to acquire the BGL based on opcode parameters)

  def CheckPrereq(self):
    """Check prerequisites.

    """
    if self.op.kind == constants.TAG_CLUSTER:
      self.target = self.cfg.GetClusterInfo()
    elif self.op.kind == constants.TAG_NODE:
      self.target = self.cfg.GetNodeInfo(self.op.name)
    elif self.op.kind == constants.TAG_INSTANCE:
      self.target = self.cfg.GetInstanceInfo(self.op.name)
    elif self.op.kind == constants.TAG_NODEGROUP:
      self.target = self.cfg.GetNodeGroup(self.group_uuid)
    elif self.op.kind == constants.TAG_NETWORK:
      self.target = self.cfg.GetNetwork(self.network_uuid)
    else:
      raise errors.OpPrereqError("Wrong tag type requested (%s)" %
                                 str(self.op.kind), errors.ECODE_INVAL)


class LUTagsGet(TagsLU):
  """Returns the tags of a given object.

  """
  REQ_BGL = False

  def ExpandNames(self):
    TagsLU.ExpandNames(self)

    # Share locks as this is only a read operation
    self.share_locks = ShareAll()

  def Exec(self, feedback_fn):
    """Returns the tag list.

    """
    return list(self.target.GetTags())


class LUTagsSearch(NoHooksLU):
  """Searches the tags for a given pattern.

  """
  REQ_BGL = False

  def ExpandNames(self):
    self.needed_locks = {}

  def CheckPrereq(self):
    """Check prerequisites.

    This checks the pattern passed for validity by compiling it.

    """
    try:
      self.re = re.compile(self.op.pattern)
    except re.error, err:
      raise errors.OpPrereqError("Invalid search pattern '%s': %s" %
                                 (self.op.pattern, err), errors.ECODE_INVAL)

  def Exec(self, feedback_fn):
    """Returns the tag list.

    """
    cfg = self.cfg
    tgts = [("/cluster", cfg.GetClusterInfo())]
    ilist = cfg.GetAllInstancesInfo().values()
    tgts.extend([("/instances/%s" % i.name, i) for i in ilist])
    nlist = cfg.GetAllNodesInfo().values()
    tgts.extend([("/nodes/%s" % n.name, n) for n in nlist])
    tgts.extend(("/nodegroup/%s" % n.name, n)
                for n in cfg.GetAllNodeGroupsInfo().values())
    results = []
    for path, target in tgts:
      for tag in target.GetTags():
        if self.re.search(tag):
          results.append((path, tag))
    return results


class LUTagsSet(TagsLU):
  """Sets a tag on a given object.

  """
  REQ_BGL = False

  def CheckPrereq(self):
    """Check prerequisites.

    This checks the type and length of the tag name and value.

    """
    TagsLU.CheckPrereq(self)
    for tag in self.op.tags:
      objects.TaggableObject.ValidateTag(tag)

  def Exec(self, feedback_fn):
    """Sets the tag.

    """
    try:
      for tag in self.op.tags:
        self.target.AddTag(tag)
    except errors.TagError, err:
      raise errors.OpExecError("Error while setting tag: %s" % str(err))
    self.cfg.Update(self.target, feedback_fn)


class LUTagsDel(TagsLU):
  """Delete a list of tags from a given object.

  """
  REQ_BGL = False

  def CheckPrereq(self):
    """Check prerequisites.

    This checks that we have the given tag.

    """
    TagsLU.CheckPrereq(self)
    for tag in self.op.tags:
      objects.TaggableObject.ValidateTag(tag)
    del_tags = frozenset(self.op.tags)
    cur_tags = self.target.GetTags()

    diff_tags = del_tags - cur_tags
    if diff_tags:
      diff_names = ("'%s'" % i for i in sorted(diff_tags))
      raise errors.OpPrereqError("Tag(s) %s not found" %
                                 (utils.CommaJoin(diff_names), ),
                                 errors.ECODE_NOENT)

  def Exec(self, feedback_fn):
    """Remove the tag from the object.

    """
    for tag in self.op.tags:
      self.target.RemoveTag(tag)
    self.cfg.Update(self.target, feedback_fn)
