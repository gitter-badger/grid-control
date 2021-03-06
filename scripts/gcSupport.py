#!/usr/bin/env python
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

import os, sys, time, fcntl, logging

# add python subdirectory from where exec was started to search path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'packages')))

from grid_control import utils
from grid_control.backends import WMS, storage
from grid_control.backends.access import AccessToken
from grid_control.backends.oslayer import OSLayer
from grid_control.config import createConfigFactory
from grid_control.job_db import Job, JobClass, JobDB
from grid_control.job_manager import JobManager
from grid_control.job_selector import ClassSelector, JobSelector
from grid_control.output_processor import FileInfoProcessor, JobInfoProcessor
from grid_control.report import Report
from grid_control.tasks import TaskModule

class DummyStream(object):
	def __init__(self, stream):
		self.__stream = stream
		self.log = []
	def write(self, data):
		self.log.append(data)
		return True
	def __getattr__(self, name):
		return self.__stream.__getattribute__(name)


def getConfig(configFile = None, configDict = {}, section = None, additional = []):
	if configDict and section:
		configDict = {section: configDict}
	config = createConfigFactory(configFile, configDict, additional = additional).getConfig()
	if section:
		return config.changeView(addSections = [section])
	return config


class Silencer(object):
	def __init__(self):
		self.saved = (sys.stdout, sys.stderr)
		sys.stdout = DummyStream(sys.stdout)
		sys.stderr = DummyStream(sys.stderr)
	def __del__(self):
		del sys.stdout
		del sys.stderr
		(sys.stdout, sys.stderr) = self.saved


class FileMutex:
	def __init__(self, lockfile):
		first = time.time()
		self.lockfile = lockfile
		while os.path.exists(self.lockfile):
			if first and (time.time() - first > 10):
				print 'Trying to aquire lock file %s ...' % lockfile
				first = False
			time.sleep(0.2)
		self.fd = open(self.lockfile, 'w')
		fcntl.flock(self.fd, fcntl.LOCK_EX)

	def __del__(self):
		fcntl.flock(self.fd, fcntl.LOCK_UN)
		try:
			if os.path.exists(self.lockfile):
				os.unlink(self.lockfile)
		except Exception:
			pass


def initGC(args):
	if len(args) > 0:
		config = getConfig(args[0])
		userSelector = None
		if len(args) != 1:
			userSelector = JobSelector.create(args[1])
		return (config.getWorkPath(), config, JobDB(config, jobSelector = userSelector))
	sys.stderr.write('Syntax: %s <config file> [<job id>, ...]\n\n' % sys.argv[0])
	sys.exit(os.EX_USAGE)


def getWorkJobs(args, selector = None):
	(workDir, config, jobDB) = initGC(args)
	return (workDir, len(jobDB), jobDB.getJobs(selector))


def getJobInfo(workDir, jobNum, retCodeFilter = lambda x: True):
	jobInfo = JobInfoProcessor().process(os.path.join(workDir, 'output', 'job_%d' % jobNum))
	if jobInfo:
		(jobNumStored, jobExitCode, jobData) = jobInfo
		if retCodeFilter(jobExitCode):
			return jobInfo


def getCMSSWInfo(tarPath):
	import tarfile, xml.dom.minidom
	# Read framework report files to get number of events
	tarFile = tarfile.open(tarPath, 'r:gz')
	fwkReports = filter(lambda x: os.path.basename(x.name) == 'report.xml', tarFile.getmembers())
	for fwkReport in map(lambda fn: tarFile.extractfile(fn), fwkReports):
		try:
			yield xml.dom.minidom.parse(fwkReport)
		except Exception:
			print 'Error while parsing %s' % tarPath
			raise


def prettySize(size):
	suffixes = [('B', 2**10), ('K', 2**20), ('M', 2**30), ('G', 2**40), ('T', 2**50)]
	for suf, lim in suffixes:
		if size > lim:
			continue
		else:
			return str(round(size / float(lim / 2**10), 2)) + suf


def parseOptions(parser):
	parser.add_option('',   '--parseable', dest='displaymode', const='parseable', action='store_const',
		help='Output tabular data in parseable format')
	parser.add_option('-P', '--pivot',     dest='displaymode', const='longlist',  action='store_const',
		help='Output pivoted tabular data')
	parser.add_option('',   '--textwidth', dest='textwidth',   default=100,
		help='Output tabular data with selected width')
	parser.add_option('-v', '--verbose',   dest='verbosity',   default=0,         action='count',
		help='Increase verbosity')
	(opts, args) = parser.parse_args()
	logging.getLogger().setLevel(logging.DEFAULT - opts.verbosity)
	utils.verbosity(opts.verbosity)
	utils.printTabular.mode = opts.displaymode
	utils.printTabular.wraplen = int(opts.textwidth)
	return (opts, args)
