#-#  Copyright 2009-2016 Karlsruhe Institute of Technology
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

from grid_control import utils
from grid_control.report import Report
from hpfwk import AbstractError, Plugin

class GUI(Plugin):
	def __init__(self, config, workflow):
		self._workflow = workflow
		self._reportClass = config.getCompositePlugin('report',
			'BasicReport', 'MultiReport', cls = Report, onChange = None)
		self._reportOpts = config.get('report options', '', onChange = None)

	def displayWorkflow(self):
		raise AbstractError()


class SimpleConsole(GUI):
	def __init__(self, config, workflow):
		GUI.__init__(self, config, workflow)
		self._report = self._reportClass.getInstance(self._workflow.jobManager.jobDB,
			self._workflow.task, configString = self._reportOpts)

	def displayWorkflow(self):
		utils.vprint(level = -1)
		self._report.display()
		utils.vprint(level = -1)
		if self._workflow.runContinuous:
			utils.vprint('Running in continuous mode. Press ^C to exit.', -1)
		self._workflow.jobCycle()
