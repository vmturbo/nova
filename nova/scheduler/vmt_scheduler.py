# Copyright (c) 2014 OpenStack Foundation
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""
VMTurbo Scheduler implementation
--------------------------------
Our scheduler works as a replacement for the default filter_scheduler

For integrating this scheduler to get Placement recommendations,
the following entries must be added in the /etc/nova/nova.conf file
under the [DEFAULT] section
------------------------------------------------------------
scheduler_driver = nova.scheduler.vmt_scheduler.VMTScheduler
vmturbo_rest_uri = <VMTurbo_IPAddress>
vmturbo_username = <VMTurbo_UserName>
vmturbo_password = <VMTurbo_Password>
------------------------------------------------------------
NOTE: 'scheduler_driver' might already be configured to the default scheduler
       Needs to be replaced if thats the case
"""

import random

from oslo.config import cfg

from nova.compute import rpcapi as compute_rpcapi
from nova import exception
from nova.openstack.common import log as logging
from nova.openstack.common.gettextutils import _
from nova.scheduler import driver

""" Imports for calls to VMTurbo """
import requests
import datetime
import time

import sys

ext_opts = [
    cfg.StrOpt('vmturbo_rest_uri',
                    default='URI',
                    help='VMTurbo Server URL'),
    cfg.StrOpt('vmturbo_username',
                    default='VMT_USER',
                    help='VMTurbo Server Username'),
    cfg.StrOpt('vmturbo_password',
                    default='VMT_PWD',
                    help='VMTurbo Server Username'),
]
CONF = cfg.CONF
CONF.import_opt('compute_topic', 'nova.compute.rpcapi')
CONF.register_opts(ext_opts)
LOG = logging.getLogger(__name__)

class VMTScheduler(driver.Scheduler):
    """
    Implements Scheduler as a node selector based on
    VMTurbo's placement recommendations.
    """

    def __init__(self, *args, **kwargs):
        super(VMTScheduler, self).__init__(*args, **kwargs)
        self.compute_rpcapi = compute_rpcapi.ComputeAPI()
        self.vmt_url = 'http://' + CONF.vmturbo_rest_uri + "/vmturbo/api"
        self.auth = (CONF.vmturbo_username, CONF.vmturbo_password)
        self.host_array = []

    def _schedule(self, context, topic, request_spec, filter_properties):
        """Picks a host that is up at random."""
        elevated = context.elevated()
        hosts = self.hosts_up(elevated, topic)
        host = ''
        if self.host_array:
            host = self.host_array.pop()
        else:
            raise Exception('VMTurbo could not schedule workload')
        if host in hosts:
            LOG.info('Host selected by VMTurbo ' + host)
        else:
            host = random.choice(hosts)
            LOG.info('Selecting random host. Check logs for reason')
            msg = _("Selecting random host")
        return host
 
    def select_destinations(self, context, request_spec, filter_properties):
        """Selects random destinations."""
        num_instances = request_spec['num_instances']
        Log.info("select_destinations overridden in VMTScheduler")
        dests = []
        return dests
 
    def schedule_run_instance(self, context, request_spec,
                              admin_password, injected_files,
                              requested_networks, is_first_time,
                              filter_properties, legacy_bdm_in_spec):
        """Create and run an instance or instances."""
        LOG.info("Running schedule_run_instance for the VMTurboScheduler")
        instance_uuids = request_spec.get('instance_uuids')

        self.reservationName = request_spec.get('instance_properties').get('display_name')#"From Response - Name"
        self.vmPrefix = "VMTReservation"#"From Response - Create Something"
        self.flavor_name = request_spec.get('instance_type').get('name')#filter_properties['name']#"From Response - m1.tiny"
        self.deploymentProfile = request_spec.get('block_device_mapping')[0].get('image_id')#"From the response - <uuid>"
        self.vmCount = request_spec.get('num_instances')#"From response"
        self.scheduler_hint = ''
        self.isSchedulerHintPresent = False
        if 'scheduler_hints' in filter_properties:
            if 'group' in filter_properties['scheduler_hints']:
                self.isSchedulerHintPresent = True
                self.scheduler_hint = filter_properties['scheduler_hints']['group']
            else:
                LOG.info('group not found in filter_properties[\'scheduler_hints\']')
        else:
            LOG.info('scheduler_hints not present in filter_properties')


        LOG.info(self.reservationName + " : " + self.vmPrefix + " : " + self.flavor_name + " : " + self.deploymentProfile
        + " : " + str(self.vmCount) + " : " + self.vmt_url + " : " + self.auth[0] + " : " + self.auth[1] + " : " + self.scheduler_hint)
        self.host_array[:] = []
        try:
            self.templateName = self.getTemplateFromUuid(self.flavor_name, self.deploymentProfile)
            reservationUuid = self.requestPlacement(self.isSchedulerHintPresent)
            LOG.info("Template UUID " + self.templateName + " : Reservation UUID " + reservationUuid)
            self.pollForStatus(reservationUuid)
            self.deletePlacement(reservationUuid)
        except:
            e = sys.exc_info()[0]
            type, value, tb = sys.exc_info()
            LOG.info('ERROR when getting responses from VMTurbo ')
            LOG.info(value.message)
        LOG.info('Hosts fetched from VMTurbo')
        LOG.info(self.host_array)
        for num, instance_uuid in enumerate(instance_uuids):
            request_spec['instance_properties']['launch_index'] = num
            try:
                host = self._schedule(context, CONF.compute_topic,
                                      request_spec, filter_properties)
                updated_instance = driver.instance_update_db(context,
                        instance_uuid)
                self.compute_rpcapi.run_instance(context,
                        instance=updated_instance, host=host,
                        requested_networks=requested_networks,
                        injected_files=injected_files,
                        admin_password=admin_password,
                        is_first_time=is_first_time,
                        request_spec=request_spec,
                        filter_properties=filter_properties,
                        legacy_bdm_in_spec=legacy_bdm_in_spec)
            except Exception as ex:
                driver.handle_schedule_error(context, ex, instance_uuid,
                                             request_spec)

    """ VMTurbo Specific calls """
    """ These calls need to be made more generic so that other """
    """ external systems can be used for scheduling tasks """

    def requestPlacement(self, isSchedulerHintPresent):
        """ Deploy date is always today """
        formatDate = "%Y-%m-%d %H:%M:%S"
        deployDate = datetime.date.today().strftime(formatDate)
        LOG.info("Creating reservation: " + self.reservationName + ". "
                       + "vmPrefix: " + self.vmPrefix + ". "
                       + "templateName: " + self.templateName + ". "
                       + "deploymentProfile: " + self.deploymentProfile + ". "
                       + "count: " + str(self.vmCount) + ". "
                       + "deployDate: " + deployDate + ".")
        requests_data_dict = dict()
        requests_data_dict.update({ "vmPrefix" : self.vmPrefix })
        requests_data_dict.update({ "reservationName" : self.reservationName })
        requests_data_dict.update({ "templateName" : self.templateName })
        requests_data_dict.update({ "count" : str(self.vmCount) })
        requests_data_dict.update({ "deploymentProfile" : self.deploymentProfile })
        requests_data_dict.update({ "deployDate" : deployDate })
        if isSchedulerHintPresent:
            requests_data_dict.update({ "segmentationUuid[]" : self.scheduler_hint })
        reservation_uuid = self.apiPost("/reservations", requests_data_dict)
        if reservation_uuid[0] == "":
            LOG.info("Reservation was not generated due to a possible misconfiguration.")
        return reservation_uuid[0]

    def getPlacementStatus(self, reservation_uuid):
        LOG.debug("Getting status for reservation: " + reservation_uuid)
        all_reservations_xml = self.apiGet("/reservations")
        for xml_line in all_reservations_xml:
            if self.parseField("uuid", xml_line) == reservation_uuid:
                status = self.parseField("status", xml_line)
                break
        else:
            LOG.info("Reservation was not found by uuid in all reservations xml.")
            status = ""
        return status

    def getTemplateFromUuid(self, flavor_name, service_uuid):
        LOG.info("Getting template uuid for serviceUuid: " + service_uuid)
        all_templates_xml = self.apiGet("/templates")
        for xml_line in all_templates_xml:
            if ((self.parseField("displayName", xml_line) == flavor_name) &
                (service_uuid in self.parseField("services", xml_line))):
                templateUuid = self.parseField("uuid", xml_line)
                break
        else:
            LOG.info("Reservation was not found by uuid in all reservations xml.")
            templateUuid = ""
        return templateUuid

    def pollForStatus(self, reservationUuid):
        statusRes = self.getPlacementStatus(reservationUuid)
        count = 0
        """ Setting the timeout to 5 mintues """
        while (statusRes == "LOADING"):
            ++count
            statusRes = self.getPlacementStatus(reservationUuid)
            time.sleep(2)
            if (count > 150):
                break
        if (statusRes == "PLACEMENT_SUCCEEDED"):
            LOG.info("Placement with uuid " + reservationUuid + " succeeded")
            self.populateResourceList(reservationUuid)
        else:
            LOG.warn("Placement with uuid " + reservationUuid + " could not be placed")
            self.host_array = []

    def deletePlacement(self, reservation_uuid):
        LOG.info("Deleting reservation." + reservation_uuid)
        response = self.apiDelete("/reservations/" + reservation_uuid)
        time.sleep(10)
        if response[0] == "true":
            LOG.debug("Delete Response returned true")
        else:
            LOG.debug("Delete Response returned false")
        return

    def populateResourceList(self, reservation_uuid):
        LOG.debug("Parsing Reservation response")
        host_array = []
        reservation_xml = self.apiGet("/reservations/" + reservation_uuid)
        for xml_line in reservation_xml:
            if "name" in xml_line:
                self.host_array.append(self.parseField("host", xml_line))

    def parseField(self, xml_field, xml_line):
        xml_field += "=\""
        fieldLength = len(xml_field)
        fieldLocation = xml_line.find(xml_field) + fieldLength
        if fieldLocation != -1 + fieldLength:
            return xml_line[fieldLocation:fieldLocation + xml_line[fieldLocation:].find("\"")]
        else:
            return ""

    def getXmlFromResponse(self, response_from_api_call):
        return filter(None, response_from_api_call.split("\n"))

    def apiGet(self, getUrl):
        fullUrl = self.vmt_url + getUrl
        response = requests.get(fullUrl, auth=self.auth)
        return self.getXmlFromResponse(response.content)

    def apiDelete(self, deleteUrl):
        fullUrl = self.vmt_url + deleteUrl
        response = requests.delete(fullUrl, auth=self.auth)
        return self.getXmlFromResponse(response.content)

    def apiPost(self, postUrl, requests_data_dict):
        fullUrl = self.vmt_url + postUrl
        response = requests.post( fullUrl , data=requests_data_dict , auth=self.auth)
        return self.getXmlFromResponse(response.content)

