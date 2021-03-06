#!/usr/bin/env python
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

import os, sys, signal, logging, optparse

# Load grid-control package
sys.path.append(os.path.abspath(os.path.join(sys.path[0], 'packages')))
from grid_control import utils
from grid_control.config import ConfigEntry, createConfigFactory
from grid_control.config.cfiller_base import ConfigFiller, StringConfigFiller
from grid_control.logging_setup import logging_setup
from grid_control.workflow import Workflow
from hpfwk import debugInterruptHandler

if __name__ == '__main__':
	global log, handler
	log = None

	# set up signal handler for interrupts
	def interrupt(sig, frame):
		global log, handler
		utils.abort(True)
		log = utils.ActivityLog('Quitting grid-control! (This can take a few seconds...)')
		signal.signal(signal.SIGINT, handler)
	handler = signal.signal(signal.SIGINT, interrupt)

	# set up signal handler for debug session requests
	signal.signal(signal.SIGURG, debugInterruptHandler)

	# display the 'grid-control' logo and version
	utils.vprint(open(utils.pathShare('logo.txt'), 'r').read(), -1)
	utils.vprint('Revision: %s' % utils.getVersion(), -1)
	pyver = sys.version_info[0] + sys.version_info[1] / 10.0
	if pyver < 2.3:
		utils.deprecated('This python version (%.1f) is not supported anymore!' % pyver)

	usage = 'Syntax: %s [OPTIONS] <config file>\n' % sys.argv[0]
	parser = optparse.OptionParser(add_help_option=False)
	parser.add_option('-h', '--help',          dest='help',       default=False, action='store_true')
	parser.add_option('',   '--help-conf',     dest='help_cfg',   default=False, action='store_true')
	parser.add_option('',   '--help-confmin',  dest='help_scfg',  default=False, action='store_true')
	parser.add_option('-i', '--init',          dest='init',       default=False, action='store_true')
	parser.add_option('-q', '--resync',        dest='resync',     default=False, action='store_true')
	parser.add_option('',   '--debug',         dest='debug',      default=False, action='store_true')
	parser.add_option('-s', '--no-submission', dest='submission', default=True,  action='store_false')
	parser.add_option('-c', '--continuous',    dest='continuous', default=None,  action='store_true')
	parser.add_option('-o', '--override',      dest='override',   default=[],    action='append')
	parser.add_option('-d', '--delete',        dest='delete',     default=None)
	parser.add_option('',   '--reset',         dest='reset',      default=None)
	parser.add_option('-a', '--action',        dest='action',     default=None)
	parser.add_option('-J', '--job-selector',  dest='selector',   default=None)
	parser.add_option('-m', '--max-retry',     dest='maxRetry',   default=None,  type='int')
	parser.add_option('-v', '--verbose',       dest='verbosity',  default=0,     action='count')
	parser.add_option('-G', '--gui',           dest='gui',        action='store_const', const = 'ANSIGUI')
	parser.add_option('-W', '--webserver',     dest='gui',        action='store_const', const = 'CPWebserver')
	# Deprecated options - refer to new report script instead
	parser.add_option('-r', '--report',        dest='old_report', default=False, action='store_true')
	parser.add_option('-R', '--site-report',   dest='old_report', default=False, action='store_true')
	parser.add_option('-T', '--time-report',   dest='old_report', default=False, action='store_true')
	parser.add_option('-M', '--task-report',   dest='old_report', default=False, action='store_true')
	parser.add_option('-D', '--detail-report', dest='old_report', default=False, action='store_true')
	parser.add_option('',   '--help-vars',     dest='old_report', default=False, action='store_true')
	(opts, args) = parser.parse_args()
	if opts.help:
		utils.eprint('%s\n%s' % (usage, open(utils.pathShare('help.txt'), 'r').read()))
		sys.exit(os.EX_USAGE)

	utils.verbosity(opts.verbosity)
	logging.getLogger().setLevel(logging.DEFAULT - opts.verbosity)
	if opts.debug:
		logging.getLogger('exception').addHandler(logging.StreamHandler(sys.stdout))

	# we need exactly one positional argument (config file)
	if len(args) != 1:
		utils.exitWithUsage(usage, 'Config file not specified!')
	if opts.old_report:
		utils.deprecated('Please use the more versatile report tool in the scripts directory!')

	# Config filler which collects data from command line arguments
	class OptsConfigFiller(ConfigFiller):
		def __init__(self, optParser):
			self._optParser = optParser

		def fill(self, container):
			entries = filter(lambda entry: entry.section == 'global', container.getEntries('cmdargs'))
			combinedEntry = ConfigEntry.combineEntries(entries)
			defaultCmdLine = []
			if combinedEntry:
				defaultCmdLine = combinedEntry.value.split()
			(opts, args) = self._optParser.parse_args(args = defaultCmdLine + sys.argv[1:])
			def setConfigFromOpt(section, option, value):
				if value != None:
					self._addEntry(container, section, option, str(value), '<cmdline>')
			for (option, value) in {'max retry': opts.maxRetry, 'action': opts.action,
					'continuous': opts.continuous, 'selected': opts.selector}.items():
				setConfigFromOpt('jobs', option, value)
			setConfigFromOpt('state!', '#init', opts.init)
			setConfigFromOpt('state!', '#resync', opts.resync)
			setConfigFromOpt('global', 'gui', opts.gui)
			setConfigFromOpt('global', 'submission', opts.submission)
			StringConfigFiller(opts.override).fill(container)

	# big try... except block to catch exceptions and print error message
	def main():
		configFactory = createConfigFactory(configFile = args[0], additional = [OptsConfigFiller(parser)])
		config = configFactory.getConfig()
		logging_setup(config.changeView(setSections = ['logging']))

		# Check work dir validity (default work directory is the config file name)
		if not os.path.exists(config.getWorkPath()):
			if not config.getState('init'):
				utils.vprint('Will force initialization of %s if continued!' % config.getWorkPath(), -1)
				config.setState(True, 'init')
			if config.getChoiceYesNo('workdir create', True,
					interactive = 'Do you want to create the working directory %s?' % config.getWorkPath()):
				utils.ensureDirExists(config.getWorkPath(), 'work directory')

		# Create workflow and freeze config settings
		globalConfig = config.changeView(setSections = ['global'])
		workflow = globalConfig.getPlugin('workflow', 'Workflow:global', cls = Workflow).getInstance()
		configFactory.freezeConfig(writeConfig = config.getState('init', detail = 'config'))

		# Give config help
		if opts.help_cfg or opts.help_scfg:
			config.write(sys.stdout, printDefault = opts.help_cfg, printUnused = False,
				printMinimal = opts.help_scfg, printSource = opts.help_cfg)
			sys.exit(os.EX_OK)

		# Check if user requested deletion / reset of jobs
		if opts.delete:
			workflow.jobManager.delete(workflow.wms, opts.delete)
			sys.exit(os.EX_OK)
		if opts.reset:
			workflow.jobManager.reset(workflow.wms, opts.reset)
			sys.exit(os.EX_OK)
		# Run the configured workflow
		workflow.run()

	sys.exit(main())
