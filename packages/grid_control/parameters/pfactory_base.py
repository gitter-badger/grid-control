#-#  Copyright 2012-2015 Karlsruhe Institute of Technology
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

import random
from grid_control.config import TaggedConfigView
from grid_control.parameters.config_param import ParameterConfig
from grid_control.parameters.padapter import ParameterAdapter
from grid_control.parameters.psource_basic import ConstParameterSource, CounterParameterSource, RNGParameterSource, RequirementParameterSource
from grid_control.parameters.psource_data import DataParameterSource
from grid_control.parameters.psource_meta import CrossParameterSource, RepeatParameterSource, ZipLongParameterSource
from hpfwk import NamedPlugin

class ParameterFactory(NamedPlugin):
	configSections = NamedPlugin.configSections + ['parameters']
	tagName = 'parameters'

	def __init__(self, config, name):
		NamedPlugin.__init__(self, config, name)
		self.adapter = config.get('parameter adapter', 'TrackedParameterAdapter')
		self.paramConfig = ParameterConfig(config.changeView(setSections = ['parameters']), self.adapter != 'TrackedParameterAdapter')


	def _getRawSource(self, parent):
		return parent


	def getSource(self, config):
		source = self._getRawSource(RNGParameterSource())
		if DataParameterSource.datasetsAvailable and not DataParameterSource.datasetsUsed:
			source = CrossParameterSource(DataParameterSource.create(), source)
		return ParameterAdapter.getInstance(self.adapter, config, source)


class BasicParameterFactory(ParameterFactory):
	def __init__(self, config, name):
		(self.constSources, self.lookupSources) = ([], [])
		ParameterFactory.__init__(self, config, name)

		# Get constants from [constants <tags...>]
		configConstants = config.changeView(viewClass = TaggedConfigView,
			setClasses = None, setSections = ['constants'], addTags = [self])
		for cName in filter(lambda o: not o.endswith(' lookup'), configConstants.getOptions()):
			self._addConstantPSource(configConstants, cName, cName.upper())
		# Get constants from [<Module>] constants
		for cName in map(str.strip, config.getList('constants', [])):
			self._addConstantPSource(config, cName, cName)
		# Random number variables
		configJobs = config.changeView(addSections = ['jobs'])
		nseeds = configJobs.getInt('nseeds', 10)
		newSeeds = map(lambda x: str(random.randint(0, 10000000)), range(nseeds))
		for (idx, seed) in enumerate(configJobs.getList('seeds', newSeeds, persistent = True)):
			self.constSources.append(CounterParameterSource('SEED_%d' % idx, int(seed)))
		self.repeat = config.getInt('repeat', 1, onChange = None) # ALL config.x -> paramconfig.x !


	def _addConstantPSource(self, config, cName, varName):
		lookupVar = config.get('%s lookup' % cName, '')
		if lookupVar:
			self.lookupSources.append(LookupParameterSource(varName, config.getDict(cName, {}), lookupVar))
		else:
			self.constSources.append(ConstParameterSource(varName, config.get(cName).strip()))


	def _getRawSource(self, parent):
		source_list = self.constSources + [parent, RequirementParameterSource()]
		source = ZipLongParameterSource(*source_list)
		if self.repeat > 1:
			source = RepeatParameterSource(source, self.repeat)
		return ParameterFactory._getRawSource(self, source)
