#!/usr/bin/python -u
#

# Copyright (C) 2013 Google Inc.
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


"""Script for providing a large amount of RAPI calls to Ganeti.

"""

# pylint: disable=C0103
# due to invalid name


import sys

from ganeti.rapi.client import GanetiApiError

import qa_config
import qa_node
import qa_rapi


# The purpose of this file is to provide a stable and extensive RAPI workload
# that manipulates the cluster only using RAPI commands, with the assumption
# that an empty cluster was set up beforehand. All the nodes that can be added
# to the cluster should be a part of it, and no instances should be present.
#
# Its intended use is in RAPI compatibility tests, where different versions with
# possibly vastly different QAs must be compared. Running the QA on both
# versions of the cluster will produce RAPI calls, but there is no guarantee
# that they will match, or that functions invoked in between will not change the
# results.
#
# By using only RAPI functions, we are sure to be able to capture and log all
# the changes in cluster state, and be able to compare them afterwards.
#
# The functionality of the QA is still used to generate a functioning,
# RAPI-enabled cluster, and to set up a C{GanetiRapiClient} capable of issuing
# commands to the cluster.
#
# Due to the fact that not all calls issued as a part of the workload might be
# implemented in the different versions of Ganeti, the client does not halt or
# produce a non-zero exit code upon encountering a RAPI error. Instead, it
# reports it and moves on. Any utility comparing the requests should account for
# this.


def MockMethod(*_args, **_kwargs):
  """ Absorbs all arguments, does nothing, returns None.

  """
  return None


def InvokerCreator(fn, name):
  """ Returns an invoker function that will invoke the given function
  with any arguments passed to the invoker at a later time, while
  catching any specific non-fatal errors we would like to know more
  about.

  @type fn arbitrary function
  @param fn The function to invoke later.
  @type name string
  @param name The name of the function, for debugging purposes.
  @rtype function

  """
  def decoratedFn(*args, **kwargs):
    result = None
    try:
      print "Using method %s" % name
      result = fn(*args, **kwargs)
    except GanetiApiError as e:
      print "RAPI error while performing function %s : %s" % \
            (name, str(e))
    return result

  return decoratedFn


RAPI_USERNAME = "ganeti-qa"


class GanetiRapiClientWrapper(object):
  """ Creates and initializes a GanetiRapiClient, and acts as a wrapper invoking
  only the methods that the version of the client actually uses.

  """
  def __init__(self):
    self._client = qa_rapi.Setup(RAPI_USERNAME,
                                 qa_rapi.LookupRapiSecret(RAPI_USERNAME))

  def __getattr__(self, attr):
    """ Fetches an attribute from the underlying client if necessary.

    """
    # Assuming that this method exposes no public methods of its own,
    # and that any private methods are named according to the style
    # guide, this will stop infinite loops in attribute fetches.
    if attr.startswith("_"):
      return self.__getattribute__(attr)
    try:
      return InvokerCreator(self._client.__getattribute__(attr), attr)
    except AttributeError:
      print "Missing method %s; supplying mock method" % attr
      return MockMethod


def Finish(client, fn, *args, **kwargs):
  """ When invoked with a job-starting RAPI client method, it passes along any
  additional arguments and waits until its completion.

  @type client C{GanetiRapiClientWrapper}
  @param client The client wrapper.
  @type fn function
  @param fn A client method returning a job id.

  """
  possible_job_id = fn(*args, **kwargs)
  try:
    # The job ids are returned as both ints and ints represented by strings.
    # This is a pythonic check to see if the content is an int.
    int(possible_job_id)
  except (ValueError, TypeError):
    # As a rule of thumb, failures will return None, and other methods are
    # expected to return at least something
    if possible_job_id is not None:
      print ("Finish called with a method not producing a job id, "
             "returning %s" % possible_job_id)
    return possible_job_id

  success = client.WaitForJobCompletion(possible_job_id)

  result = client.GetJobStatus(possible_job_id)["opresult"][0]
  if success:
    return result
  else:
    print "Error encountered while performing operation: "
    print result
    return None


def TestTags(client, get_fn, add_fn, delete_fn, *args):
  """ Tests whether tagging works.

  @type client C{GanetiRapiClientWrapper}
  @param client The client wrapper.
  @type get_fn function
  @param get_fn A Get*Tags function of the client.
  @type add_fn function
  @param add_fn An Add*Tags function of the client.
  @type delete_fn function
  @param delete_fn A Delete*Tags function of the client.

  To allow this method to work for all tagging functions of the client, use
  named methods.

  """
  get_fn(*args)

  tags = ["tag1", "tag2", "tag3"]
  Finish(client, add_fn, *args, tags=tags, dry_run=True)
  Finish(client, add_fn, *args, tags=tags)

  get_fn(*args)

  Finish(client, delete_fn, *args, tags=tags[:1], dry_run=True)
  Finish(client, delete_fn, *args, tags=tags[:1])

  get_fn(*args)

  Finish(client, delete_fn, *args, tags=tags[1:])

  get_fn(*args)


def TestGetters(client):
  """ Tests the various get functions which only retrieve information about the
  cluster.

  @type client C{GanetiRapiClientWrapper}

  """
  client.GetVersion()
  client.GetFeatures()
  client.GetOperatingSystems()
  client.GetInfo()
  client.GetClusterTags()
  client.GetInstances()
  client.GetInstances(bulk=True)
  client.GetJobs()
  client.GetJobs(bulk=True)
  client.GetNodes()
  client.GetNodes(bulk=True)
  client.GetNetworks()
  client.GetNetworks(bulk=True)
  client.GetGroups()
  client.GetGroups(bulk=True)


def RemoveAllInstances(client):
  """ Queries for a list of instances, then removes them all.

  @type client C{GanetiRapiClientWrapper}
  @param client A wrapped RAPI client.

  """
  instances = client.GetInstances()
  for inst in instances:
    Finish(client, client.DeleteInstance, inst)

  instances = client.GetInstances()
  assert len(instances) == 0


def TestSingleInstance(client, instance_name, alternate_name, node_one,
                       node_two):
  """ Creates an instance, performs operations involving it, and then deletes
  it.

  @type client C{GanetiRapiClientWrapper}
  @param client A wrapped RAPI client.
  @type instance_name string
  @param instance_name The hostname to use.
  @type instance_name string
  @param instance_name Another valid hostname to use.
  @type node_one string
  @param node_one A node on which an instance can be added.
  @type node_two string
  @param node_two A node on which an instance can be added.

  """

  # Check that a dry run works, use string with size and unit
  Finish(client, client.CreateInstance,
         "create", instance_name, "plain", [{"size":"1gb"}], [], dry_run=True,
          os="debian-image", pnode=node_one)

  # Another dry run, numeric size, should work, but still a dry run
  Finish(client, client.CreateInstance,
         "create", instance_name, "plain", [{"size": "1000"}], [{}],
         dry_run=True, os="debian-image", pnode=node_one)

  # Create a smaller instance, and delete it immediately
  Finish(client, client.CreateInstance,
         "create", instance_name, "plain", [{"size":800}], [{}],
         os="debian-image", pnode=node_one)

  Finish(client, client.DeleteInstance, instance_name)

  # Create one instance to use in further tests
  Finish(client, client.CreateInstance,
         "create", instance_name, "plain", [{"size":1200}], [{}],
         os="debian-image", pnode=node_one)

  client.GetInstance(instance_name)

  Finish(client, client.GetInstanceInfo, instance_name)

  Finish(client, client.GetInstanceInfo, instance_name, static=True)

  TestTags(client, client.GetInstanceTags, client.AddInstanceTags,
           client.DeleteInstanceTags, instance_name)

  Finish(client, client.GrowInstanceDisk,
         instance_name, 0, 100, wait_for_sync=True)

  Finish(client, client.RebootInstance,
         instance_name, "soft", ignore_secondaries=True, dry_run=True,
         reason="Hulk smash gently!")

  Finish(client, client.ShutdownInstance,
         instance_name, dry_run=True, no_remember=False,
         reason="Hulk smash hard!")

  Finish(client, client.StartupInstance,
         instance_name, dry_run=True, no_remember=False,
         reason="Not hard enough!")

  Finish(client, client.RebootInstance,
         instance_name, "soft", ignore_secondaries=True, dry_run=False)

  Finish(client, client.ShutdownInstance,
         instance_name, dry_run=False, no_remember=False)

  Finish(client, client.ModifyInstance,
         instance_name, disk_template="drbd", remote_node=node_two)

  Finish(client, client.ModifyInstance,
         instance_name, disk_template="plain")

  Finish(client, client.RenameInstance,
         instance_name, alternate_name, ip_check=True, name_check=True)

  Finish(client, client.RenameInstance, alternate_name, instance_name)

  Finish(client, client.DeactivateInstanceDisks, instance_name)

  Finish(client, client.ActivateInstanceDisks, instance_name)

  Finish(client, client.RecreateInstanceDisks,
         instance_name, [0], [node_one])

  Finish(client, client.StartupInstance,
         instance_name, dry_run=False, no_remember=False)

  client.GetInstanceConsole(instance_name)

  Finish(client, client.ReinstallInstance,
         instance_name, os=None, no_startup=False, osparams={})

  Finish(client, client.DeleteInstance, instance_name, dry_run=True)

  Finish(client, client.DeleteInstance, instance_name)


def Workload(client):
  """ The actual RAPI workload used for tests.

  @type client C{GanetiRapiClientWrapper}
  @param client A wrapped RAPI client.

  """

  # First just the simple information retrievals
  TestGetters(client)

  # Then the only remaining function which is parameter-free
  Finish(client, client.RedistributeConfig)

  TestTags(client, client.GetClusterTags, client.AddClusterTags,
           client.DeleteClusterTags)

  # Generously assume the master is present
  node = qa_config.AcquireNode()
  TestTags(client, client.GetNodeTags, client.AddNodeTags,
           client.DeleteNodeTags, node.primary)
  node.Release()

  # Instance tests

  # First remove all instances the QA might have created
  RemoveAllInstances(client)

  nodes = qa_config.AcquireManyNodes(2)
  instance_one = qa_config.AcquireInstance()
  instance_two = qa_config.AcquireInstance()
  TestSingleInstance(client, instance_one.name, instance_two.name,
                     nodes[0].primary, nodes[1].primary)
  instance_two.Release()
  instance_one.Release()
  qa_config.ReleaseManyNodes(nodes)


def Usage():
  sys.stderr.write("Usage:\n\trapi-workload.py qa-config-file")


def Main():
  if len(sys.argv) < 2:
    Usage()

  qa_config.Load(sys.argv[1])

  # Only the master will be present after a fresh QA cluster setup, so we have
  # to invoke this to get all the other nodes.
  qa_node.TestNodeAddAll()

  client = GanetiRapiClientWrapper()

  Workload(client)

  qa_node.TestNodeRemoveAll()


if __name__ == "__main__":
  Main()
