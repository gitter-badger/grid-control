#-#  Copyright 2007-2016 Karlsruhe Institute of Technology
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

import os, random
from grid_control import utils
from grid_control.backends import WMS
from grid_control.config import TaggedConfigView, changeInitNeeded
from grid_control.parameters import ParameterFactory, ParameterInfo
from hpfwk import AbstractError, NamedPlugin
from time import strftime, time
from python_compat import lru_cache, md5

class TaskModule(NamedPlugin):
	configSections = NamedPlugin.configSections + ['task']
	tagName = 'task'

	# Read configuration options and init vars
	def __init__(self, config, name):
		NamedPlugin.__init__(self, config, name)
		initSandbox = changeInitNeeded('sandbox')

		# Task requirements
		configJobs = config.changeView(viewClass = TaggedConfigView, addSections = ['jobs'], addTags = [self]) # Move this into parameter manager?
		self.wallTime = configJobs.getTime('wall time', onChange = None)
		self.cpuTime = configJobs.getTime('cpu time', self.wallTime, onChange = None)
		self.cpus = configJobs.getInt('cpus', 1, onChange = None)
		self.memory = configJobs.getInt('memory', -1, onChange = None)
		self.nodeTimeout = configJobs.getTime('node timeout', -1, onChange = initSandbox)

		# Compute / get task ID
		self.taskID = config.get('task id', 'GC' + md5(str(time())).hexdigest()[:12], persistent = True)
		self.taskDate = config.get('task date', strftime('%Y-%m-%d'), persistent = True, onChange = initSandbox)
		self.taskConfigName = config.getConfigName()

		# Storage setup
		configStorage = config.changeView(viewClass = TaggedConfigView,
			setClasses = None, setNames = None, addSections = ['storage'], addTags = [self])
		self.taskVariables = {
			# Space limits
			'SCRATCH_UL': configStorage.getInt('scratch space used', 5000, onChange = initSandbox),
			'SCRATCH_LL': configStorage.getInt('scratch space left', 1, onChange = initSandbox),
			'LANDINGZONE_UL': configStorage.getInt('landing zone space used', 100, onChange = initSandbox),
			'LANDINGZONE_LL': configStorage.getInt('landing zone space left', 1, onChange = initSandbox),
		}
		configStorage.set('se output pattern', 'job_@GC_JOB_ID@_@X@')
		self.seMinSize = configStorage.getInt('se min size', -1, onChange = initSandbox)

		self.sbInputFiles = config.getPaths('input files', [], onChange = initSandbox)
		self.sbOutputFiles = config.getList('output files', [], onChange = initSandbox)
		self.gzipOut = config.getBool('gzip output', True, onChange = initSandbox)

		self.substFiles = config.getList('subst files', [], onChange = initSandbox)
		self.dependencies = map(str.lower, config.getList('depends', [], onChange = initSandbox))

		# Get error messages from gc-run.lib comments
		self.errorDict = dict(self.updateErrorDict(utils.pathShare('gc-run.lib')))

		# Init parameter source manager
		pm = config.getPlugin('parameter factory', 'SimpleParameterFactory',
			cls = ParameterFactory, inherit = True).getInstance()
		configParam = config.changeView(viewClass = TaggedConfigView, addSections = ['parameters'], addTags = [self])
		self.setupJobParameters(configParam, pm)
		self.source = pm.getSource(configParam)


	def setupJobParameters(self, config, pm):
		pass


	# Read comments with error codes at the beginning of file
	def updateErrorDict(self, fileName):
		for line in filter(lambda x: x.startswith('#'), open(fileName, 'r').readlines()):
			try:
				transform = lambda (x, y): (int(x.strip('# ')), y)
				yield transform(map(str.strip, line.split(' - ', 1)))
			except Exception:
				pass


	# Get environment variables for gc_config.sh
	def getTaskConfig(self):
		taskConfig = {
			# Storage element
			'SE_MINFILESIZE': self.seMinSize,
			# Sandbox
			'SB_OUTPUT_FILES': str.join(' ', self.getSBOutFiles()),
			'SB_INPUT_FILES': str.join(' ', map(lambda x: x.pathRel, self.getSBInFiles())),
			# Runtime
			'GC_JOBTIMEOUT': self.nodeTimeout,
			'GC_RUNTIME': self.getCommand(),
			# Seeds and substitutions
			'SUBST_FILES': str.join(' ', map(os.path.basename, self.getSubstFiles())),
			# Task infos
			'GC_TASK_CONF': self.taskConfigName,
			'GC_TASK_DATE': self.taskDate,
			'GC_TASK_ID': self.taskID,
			'GC_VERSION': utils.getVersion(),
		}
		return utils.mergeDicts([taskConfig, self.taskVariables])
	getTaskConfig = lru_cache(getTaskConfig)


	# Get job dependent environment variables
	def getJobConfig(self, jobNum):
		tmp = self.source.getJobInfo(jobNum)
		return dict(map(lambda key: (key, tmp.get(key, '')), self.source.getJobKeys()))


	def getTransientVars(self):
		hx = str.join("", map(lambda x: "%02x" % x, map(random.randrange, [256]*16)))
		return {'GC_DATE': strftime("%F"), 'GC_TIMESTAMP': strftime("%s"),
			'GC_GUID': '%s-%s-%s-%s-%s' % (hx[:8], hx[8:12], hx[12:16], hx[16:20], hx[20:]),
			'RANDOM': str(random.randrange(0, 900000000))}


	def getVarNames(self):
		# Take task variables and the variables from the parameter source
		return self.getTaskConfig().keys() + list(self.source.getJobKeys())


	def getVarMapping(self):
		# Transient variables
		transients = ['GC_DATE', 'GC_TIMESTAMP', 'GC_GUID'] # these variables are determined on the WN
		# Alias vars: Eg. __MY_JOB__ will access $GC_JOB_ID - used mostly for compatibility
		alias = {'DATE': 'GC_DATE', 'TIMESTAMP': 'GC_TIMESTAMP', 'GUID': 'GC_GUID',
			'MY_JOBID': 'GC_JOB_ID', 'MY_JOB': 'GC_JOB_ID', 'JOBID': 'GC_JOB_ID',
			'CONF': 'GC_CONF', 'TASK_ID': 'GC_TASK_ID'}
		varNames = self.getVarNames() + transients
		alias.update(zip(varNames, varNames)) # include reflexive mappings
		return alias


	def substVars(self, inp, jobNum = None, addDict = {}, check = True):
		allVars = utils.mergeDicts([addDict, self.getTaskConfig()])
		if jobNum != None:
			allVars.update(self.getJobConfig(jobNum))
		subst = lambda x: utils.replaceDict(x, allVars, self.getVarMapping().items() + zip(addDict, addDict))
		result = subst(subst(str(inp)))
		return utils.checkVar(result, "'%s' contains invalid variable specifiers: '%s'" % (inp, result), check)


	def validateVariables(self):
		for x in self.getTaskConfig().values() + self.getJobConfig(0).values():
			self.substVars(x, 0, dict.fromkeys(['X', 'XBASE', 'XEXT', 'GC_DATE', 'GC_TIMESTAMP', 'GC_GUID', 'RANDOM'], ''))


	# Get job requirements
	def getRequirements(self, jobNum):
		return [
			(WMS.WALLTIME, self.wallTime),
			(WMS.CPUTIME, self.cpuTime),
			(WMS.MEMORY, self.memory),
			(WMS.CPUS, self.cpus)
		] + self.source.getJobInfo(jobNum)[ParameterInfo.REQS]


	def getSEInFiles(self):
		return []


	# Get files for input sandbox
	def getSBInFiles(self):
		return map(lambda fn: utils.Result(pathAbs = fn, pathRel = os.path.basename(fn)), self.sbInputFiles)


	# Get files for output sandbox
	def getSBOutFiles(self):
		return list(self.sbOutputFiles)


	# Get files whose content will be subject to variable substitution
	def getSubstFiles(self):
		return list(self.substFiles)


	def getCommand(self):
		raise AbstractError


	def getJobArguments(self, jobNum):
		return ''


	def getMaxJobs(self):
		return self.source.getMaxJobs()


	def getDependencies(self):
		return list(self.dependencies)


	def getDescription(self, jobNum): # (task name, job name, job type)
		return utils.Result(taskName = self.taskID,
			jobName = self.taskID[:10] + '.' + str(jobNum), jobType = None)


	def report(self, jobNum):
		keys = filter(lambda k: k.untracked == False, self.source.getJobKeys())
		return utils.filterDict(self.source.getJobInfo(jobNum), kF = lambda k: k in keys)


	def canFinish(self):
		return True


	def canSubmit(self, jobNum):
		return self.source.canSubmit(jobNum)


	# Called on job submission
	def getSubmitInfo(self, jobNum):
		return {}


	# Intervene in job management - return None or (redoJobs, disableJobs)
	def getIntervention(self):
		return self.source.resync()
