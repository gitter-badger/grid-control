#-#  Copyright 2009-2015 Karlsruhe Institute of Technology
#-#
#-#  Licensed under the Apache License, Version 2.0 (the "License");
#-#  you may not use this file except in compliance with the License.
#-#  You may obtain a copy of the License at
#-#
#-#      http://www.apache.org/licenses/LICENSE-2.0
#-#
#-#  Unless required by applicable law or agreed to in writing, software
#-#  distributed under the License is distributed on an "AS IS" BASIS,
#-#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#-#  See the License for the specific language governing permissions and
#-#  limitations under the License.

import os, xml.dom.minidom
from grid_control import utils
from grid_control.backends.wms import BackendError, WMS
from grid_control.backends.wms_pbsge import PBSGECommon
from grid_control.config import ConfigError
from grid_control.job_db import Job
from python_compat import set

class GridEngine(PBSGECommon):
	configSections = PBSGECommon.configSections + ['OGE']
	def __init__(self, config, name):
		PBSGECommon.__init__(self, config, name)
		self._user = config.get('user', os.environ.get('LOGNAME', ''), onChange = None)
		self._project = config.get('project name', '', onChange = None)
		self._configExec = utils.resolveInstallPath('qconf')


	def getSubmitArguments(self, jobNum, jobName, reqs, sandbox, stdout, stderr):
		timeStr = lambda s: '%02d:%02d:%02d' % (s / 3600, (s / 60) % 60, s % 60)
		reqMap = { WMS.MEMORY: ('h_vmem', lambda m: '%dM' % m),
			WMS.WALLTIME: ('s_rt', timeStr), WMS.CPUTIME: ('h_cpu', timeStr) }
		# Restart jobs = no
		params = ' -r n'
		if self._project:
			params += ' -P %s' % self._project
		# Job requirements
		(queue, nodes) = (reqs.get(WMS.QUEUES, [''])[0], reqs.get(WMS.SITES))
		if not nodes and queue:
			params += ' -q %s' % queue
		elif nodes and queue:
			params += ' -q %s' % str.join(',', map(lambda node: '%s@%s' % (queue, node), nodes))
		elif nodes:
			raise ConfigError('Please also specify queue when selecting nodes!')
		return params + PBSGECommon.getSubmitArguments(self, jobNum, jobName, reqs, sandbox, stdout, stderr, reqMap)


	def parseSubmitOutput(self, data):
		# Your job 424992 ("test.sh") has been submitted
		return data.split()[2].strip()


	def parseStatus(self, status):
		try:
			dom = xml.dom.minidom.parseString(str.join('', status))
		except Exception:
			raise BackendError("Couldn't parse qstat XML output!")
		for jobentry in dom.getElementsByTagName('job_list'):
			jobinfo = {}
			try:
				for node in jobentry.childNodes:
					if node.nodeType != xml.dom.minidom.Node.ELEMENT_NODE:
						continue
					if node.hasChildNodes():
						jobinfo[str(node.nodeName)] = str(node.childNodes[0].nodeValue)
				jobinfo['id'] = jobinfo['JB_job_number']
				jobinfo['status'] = jobinfo['state']
				jobinfo['dest'] = 'N/A'
				if 'queue_name' in jobinfo:
					queue, node = jobinfo['queue_name'].split('@')
					jobinfo['dest'] = '%s/%s' % (node, queue)
			except Exception:
				raise BackendError('Error reading job info:\n%s' % jobentry.toxml())
			yield jobinfo


	def parseJobState(self, state):
		if True in map(lambda x: x in state, ['h', 's', 'S', 'T', 'w']):
			return Job.QUEUED
		if True in map(lambda x: x in state, ['r', 't']):
			return Job.RUNNING
		return Job.READY


	def getCheckArguments(self, wmsIds):
		return '-xml' + utils.QM(self._user, ' -u %s' % self._user, '')


	def getCancelArguments(self, wmsIds):
		return str.join(',', wmsIds)


	def getQueues(self):
		queues = {}
		tags = ['h_vmem', 'h_cpu', 's_rt']
		reqs = dict(zip(tags, [WMS.MEMORY, WMS.CPUTIME, WMS.WALLTIME]))
		parser = dict(zip(tags, [int, utils.parseTime, utils.parseTime]))

		for queue in map(str.strip, utils.LoggedProcess(self._configExec, '-sql').iter()):
			queues[queue] = dict()
			for line in utils.LoggedProcess(self._configExec, '-sq %s' % queue).iter():
				attr, value = map(str.strip, line.split(' ', 1))
				if (attr in tags) and (value != 'INFINITY'):
					queues[queue][reqs[attr]] = parser[attr](value)
		return queues


	def getNodes(self):
		(result, active) = (set(), False)
		for group in utils.LoggedProcess(self._configExec, '-shgrpl').iter():
			result.add(group.strip())
			for host in utils.LoggedProcess(self._configExec, '-shgrp_resolved %s' % group).iter():
				result.update(host.split())
		if len(result) > 0:
			return list(result)


class UGE(GridEngine): # Univa Grid Engine
	configSections = GridEngine.configSections + ['UGE']

class OGE(GridEngine): # Oracle Grid Engine
	configSections = GridEngine.configSections + ['OGE']

class SGE(GridEngine): # Sun Grid Engine
	configSections = GridEngine.configSections + ['SGE']
