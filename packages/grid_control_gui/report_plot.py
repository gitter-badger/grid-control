#!/usr/bin/env python
#-#  Copyright 2009-2014 Karlsruhe Institute of Technology
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

# produces plots showing the Jobs performance
#
# run like this to plot all successful jobs:
#
# scripts/report.py <config file> --report PlotReport --job-selector state:SUCCESS
#
# add the option --use-task if you want the plotting script to load data like event count
# per job from the configuration

import matplotlib
import matplotlib.pyplot as plt
import os, re, numpy

#from gcSupport import getJobInfo
from grid_control import utils
from grid_control.output_processor import JobInfoProcessor
from grid_control.report import Report

JobResultEnum = utils.makeEnum([
	"TIMESTAMP_WRAPPER_START",
	"TIMESTAMP_DEPLOYMENT_START",
	"TIMESTAMP_DEPLOYMENT_DONE",
	"TIMESTAMP_SE_IN_START",
	"TIMESTAMP_SE_IN_DONE",
	"TIMESTAMP_CMSSW_CMSRUN1_START",
	"TIMESTAMP_CMSSW_CMSRUN1_DONE",
	"TIMESTAMP_EXECUTION_START",
	"TIMESTAMP_EXECUTION_DONE",
	"TIMESTAMP_SE_OUT_START",
	"TIMESTAMP_SE_OUT_DONE",
	"TIMESTAMP_WRAPPER_DONE",
	"FILESIZE_IN_TOTAL",
	"FILESIZE_OUT_TOTAL",
	"EVENT_COUNT"])


def extractJobTiming(jInfo, task ):
	jobResult = dict()
	jobNum = jInfo[0]

	# intialize all with None
	for key in JobResultEnum.enumNames:
		enumID = JobResultEnum.str2enum(key)
		jobResult[enumID] = None

	total_size_in = 0
	total_size_out = 0
	for ( key, val ) in jInfo[2].iteritems():
		enumID = JobResultEnum.str2enum(key)
		if (enumID is not None):
			jobResult[enumID] = val

		# look for file size information
		if re.match("OUTPUT_FILE_._SIZE", key):
			total_size_out = total_size_out + int(val)
		if re.match("INPUT_FILE_._SIZE", key):
			total_size_in = total_size_in + int(val)


	jobResult[JobResultEnum.FILESIZE_OUT_TOTAL] = total_size_out
	jobResult[JobResultEnum.FILESIZE_IN_TOTAL] = total_size_in

	# look for processed events, if available
	if ( task != None ):
		jobConfig = task.getJobConfig( jobNum )
		jobResult[JobResultEnum.EVENT_COUNT] = int( jobConfig["MAX_EVENTS"] )
		# make sure an undefined max event count ( -1 in cmssw ) is treated
		# as unkown event count
		if ( jobResult[JobResultEnum.EVENT_COUNT] < 0 ):
			jobResult[JobResultEnum.EVENT_COUNT] = None
	else:
		jobResult[JobResultEnum.EVENT_COUNT] = None

	return jobResult


# returns the job payload runtime in seconds
# - if a CMSSW job was run, only the time spend in the actual
#   cmsRun call will be reported
# - if a user job was run, the execution time of the user job
#   will be reported
def getPayloadRuntime(jobInfo):
	if ( jobInfo[JobResultEnum.TIMESTAMP_CMSSW_CMSRUN1_START] is not None ) and \
			( jobInfo[JobResultEnum.TIMESTAMP_CMSSW_CMSRUN1_DONE] is not None ):
		return jobInfo[JobResultEnum.TIMESTAMP_CMSSW_CMSRUN1_DONE] - \
			   jobInfo[JobResultEnum.TIMESTAMP_CMSSW_CMSRUN1_START]

	return jobInfo[JobResultEnum.TIMESTAMP_EXECUTION_DONE] - \
		   jobInfo[JobResultEnum.TIMESTAMP_EXECUTION_START]


def getSeOutRuntime(jobInfo):
	return jobInfo[JobResultEnum.TIMESTAMP_SE_OUT_DONE] - \
		   jobInfo[JobResultEnum.TIMESTAMP_SE_OUT_START]


def getSeInRuntime(jobInfo):
	return jobInfo[JobResultEnum.TIMESTAMP_SE_IN_DONE] - \
		   jobInfo[JobResultEnum.TIMESTAMP_SE_IN_START]


def getJobRuntime(jobInfo):
	return jobInfo[JobResultEnum.TIMESTAMP_WRAPPER_DONE] - \
		   jobInfo[JobResultEnum.TIMESTAMP_WRAPPER_START]


# note: can return None if no event count could be
# determined for the job
def getEventCount(jobInfo):
	return jobInfo[JobResultEnum.EVENT_COUNT]


def getEventRate(jobInfo):
	if (getPayloadRuntime(jobInfo) > 0) and (getEventCount(jobInfo) != None):
		return getEventCount(jobInfo) / ( getPayloadRuntime(jobInfo) / 60.0 )
	else:
		return None


def getSeOutBandwidth(jobInfo):
	seOutTime = getSeOutRuntime(jobInfo)
	fileSize = jobInfo[JobResultEnum.FILESIZE_OUT_TOTAL]

	if ( seOutTime > 0 ):
		return fileSize / seOutTime
	else:
		return None


# in MB
def getSeOutFilesize(jobInfo):
	return jobInfo[JobResultEnum.FILESIZE_OUT_TOTAL] / 1000000.0


# in MB
def getSeInFilesize(jobInfo):
	return jobInfo[JobResultEnum.FILESIZE_IN_TOTAL] / 1000000.0


def getSeInBandwidth(jobInfo):
	seInTime = getSeInRuntime(jobInfo)
	fileSize = jobInfo[JobResultEnum.FILESIZE_IN_TOTAL]

	if ( seInTime > 0 ):
		return fileSize / seInTime
	else:
		return None


def getSeOutAverageBandwithAtTimeSpan(jobInfo, timeStart, timeEnd):
	if getSeOutRuntime(jobInfo) > 0:
		return getQuantityAtTimeSpan(jobInfo, timeStart, timeEnd,
									 lambda jinf: (jinf[JobResultEnum.TIMESTAMP_SE_OUT_START],
												   jinf[JobResultEnum.TIMESTAMP_SE_OUT_DONE] ),
									 lambda jinf: getSeOutBandwidth(jinf))
	else:
		return None


def getSeOutActiveAtTimeSpan(jobInfo, timeStart, timeEnd):
	if getSeOutRuntime(jobInfo) > 0:
		return getQuantityAtTimeSpan(jobInfo, timeStart, timeEnd,
									 lambda jinf: (jinf[JobResultEnum.TIMESTAMP_SE_OUT_START],
												   jinf[JobResultEnum.TIMESTAMP_SE_OUT_DONE] ),
									 lambda jinf: 1.0)
	else:
		return None


def getSeInAverageBandwithAtTimeSpan(jobInfo, timeStart, timeEnd):
	if getSeInRuntime(jobInfo) > 0:
		return getQuantityAtTimeSpan(jobInfo, timeStart, timeEnd,
									 lambda jinf: (jinf[JobResultEnum.TIMESTAMP_SE_IN_START],
												   jinf[JobResultEnum.TIMESTAMP_SE_IN_DONE] ),
									 lambda jinf: getSeInBandwidth(jinf))
	else:
		return None


def getSeInActiveAtTimeSpan(jobInfo, timeStart, timeEnd):
	if getSeInRuntime(jobInfo) > 0:
		return getQuantityAtTimeSpan(jobInfo, timeStart, timeEnd,
									 lambda jinf: (jinf[JobResultEnum.TIMESTAMP_SE_IN_START],
												   jinf[JobResultEnum.TIMESTAMP_SE_IN_DONE] ),
									 lambda jinf: 1.0)
	else:
		return None


# cumulated transfer sizes

def getSeOutSizeAtTimeSpan(jobInfo, timeStart, timeEnd):
	if getSeOutRuntime(jobInfo) > 0:
		return getCumQuantityAtTimeSpan(jobInfo, timeStart, timeEnd,
										lambda jinf: (jinf[JobResultEnum.TIMESTAMP_SE_OUT_START],
													  jinf[JobResultEnum.TIMESTAMP_SE_OUT_DONE] ),
										lambda jinf: jobInfo[JobResultEnum.FILESIZE_OUT_TOTAL] / 1000.0)
	else:
		return None


def getSeInSizeAtTimeSpan(jobInfo, timeStart, timeEnd):
	if getSeInRuntime(jobInfo) > 0:
		return getCumQuantityAtTimeSpan(jobInfo, timeStart, timeEnd,
										lambda jinf: (jinf[JobResultEnum.TIMESTAMP_SE_IN_START],
													  jinf[JobResultEnum.TIMESTAMP_SE_IN_DONE] ),
										lambda jinf: jobInfo[JobResultEnum.FILESIZE_IN_TOTAL] / 1000.0)
	else:
		return None


def getJobActiveAtTimeSpan(jobInfo, timeStart, timeEnd):
	if getJobRuntime(jobInfo) > 0:
		return getQuantityAtTimeSpan(jobInfo, timeStart, timeEnd,
									 lambda jinf: (jinf[JobResultEnum.TIMESTAMP_WRAPPER_START],
												   jinf[JobResultEnum.TIMESTAMP_WRAPPER_DONE] ),
									 lambda jinf: 1.0)
	else:
		return None


def getEventRateAtTimeSpan(jobInfo, timeStart, timeEnd):
	if getJobRuntime(jobInfo) > 0:
		return getQuantityAtTimeSpan(jobInfo, timeStart, timeEnd,
									 lambda jinf: (jinf[JobResultEnum.TIMESTAMP_CMSSW_CMSRUN1_START],
												   jinf[JobResultEnum.TIMESTAMP_CMSSW_CMSRUN1_DONE] ),
									 lambda jinf: getEventRate(jinf))
	else:
		return None


def getCompleteRateAtTimeSpan(jobInfo, timeStart, timeEnd):
	if getJobRuntime(jobInfo) > 0:
		return getCumQuantityAtTimeSpan(jobInfo, timeStart, timeEnd,
										lambda jinf: (jinf[JobResultEnum.TIMESTAMP_WRAPPER_START],
													  jinf[JobResultEnum.TIMESTAMP_WRAPPER_DONE] ),
										lambda jinf: 1.0,
										useEndtime=True)
	else:
		return None


def getQuantityAtTimeSpan(jobInfo, timeStart, timeEnd, timingExtract, quantityExtract):
	assert ( timeStart <= timeEnd )

	(theStart, theEnd) = timingExtract(jobInfo)

	# will be positive if there is overlap to the left
	leftOutside = max(0, theStart - timeStart)
	# will be positive if there is overlap to the right
	rightOutside = max(0, timeEnd - theEnd)
	totalOutside = leftOutside + rightOutside
	fractionOutside = float(totalOutside) / float(timeEnd - timeStart)
	fractionOutside = min(1.0, fractionOutside)

	q = quantityExtract(jobInfo)
	if q == None:
		return None

	return q * ( 1.0 - fractionOutside )


# note: timeStart must be before all timestamps evaluated here
def getCumQuantityAtTimeSpan(jobInfo, timeStart, timeEnd, timingExtract, quantityExtract, useEndtime=False):
	assert ( timeStart < timeEnd )

	(theStart, theEnd) = timingExtract(jobInfo)

	# simpler version, which does not interpolated between timeslices
	if useEndtime:
		if ( theEnd < timeStart ):
			return quantityExtract(jobInfo)
		else:
			return 0

	theSpan = theEnd - theStart
	distFront = theStart - timeEnd
	distBack = theEnd - timeEnd

	# current timeslice ends between theStart & theEnd
	# compute ratio of covered quantity
	if distFront < 0 and distBack >= 0:
		partCovered = distBack / theSpan
	# current timeslice ends after theStart & theEnd
	elif distFront < 0 and distBack < 0:
		partCovered = 1.0
	# current timeslice ends before theStart & theEnd
	else:
		partCovered = 0.0

	return quantityExtract(jobInfo) * partCovered


class PlotReport(Report):
	def initHistogram(self, name, xlabel, ylabel):
		fig = plt.figure()

		ax = fig.add_subplot(111)
		ax.set_xlabel(xlabel)  #, ha="left" )
		# y = 0.8 will move the label more to the center
		ax.set_ylabel(ylabel, va="top", y=0.75, labelpad=20.0)

		return (name, fig, ax)

	def finalizeHistogram(self, plotSet, useLegend=False):
		if ( useLegend ):
			plt.legend(loc="upper right", numpoints=1, frameon=False, ncol=2)

		self.imageTypes = ["png", "pdf"]
		for it in self.imageTypes:
			plt.savefig(plotSet[0] + "." + it)

	def plotHistogram(self, histo, jobResult, extractor):
		print "Plotting " + histo[0] + " ..."
		runtime = []
		for res in jobResult:
			val = extractor(res)
			if val is not None:
				runtime = runtime + [val]

		if len(runtime) == 0:
			print "Skipping " + histo[0] + ", no input data"
			return None

		thisHistType = "bar"
		# if transparent:
		#	thisHistType = "step"

		pl = plt.hist(runtime, 40)  # , color= plotColor, label = plotLabel, histtype=thisHistType )
		# histo[2].set_ymargin( 0.4 )
		print " done"
		return pl

	def plotOverall(self, histo, jInfos, timespan, extractor, fit=False, unit="MB/s", cumulate=False):

		print "Plotting " + histo[0] + " ..."

		overAllBandwidth = []
		# not the first and last 5 percent, used for fitting
		truncatedOverAllBandwidth = []

		timeStep = []
		truncatedTimeStep = []

		( minTime, maxTime) = timespan
		trunctationFractionFront = 0.05
		trunctationFractionBack = 0.3

		relTimeSpan = ( maxTime - minTime)
		# compute the amount of slices, for a small timespan, use every step
		# for large timespans, use 1000 slices at most
		slices = min(1000.0, relTimeSpan)
		stepSize = int(relTimeSpan / slices)
		truncFront = relTimeSpan * trunctationFractionFront
		truncBack = relTimeSpan * ( 1.0 - trunctationFractionBack )

		for i in range(minTime, maxTime + 1, stepSize):
			thisBw = 0
			currentTimeStep = i - minTime
			timeStep = timeStep + [currentTimeStep]

			for jinfo in jInfos:
				if cumulate:
					val = extractor(jinfo, minTime, i + 1)
				else:
					val = extractor(jinfo, i, i + stepSize)

				if val is not None:
					thisBw = thisBw + val

			overAllBandwidth = overAllBandwidth + [thisBw]
			if (currentTimeStep > truncFront) and (currentTimeStep < truncBack):
				truncatedOverAllBandwidth = truncatedOverAllBandwidth + [thisBw]
				truncatedTimeStep = truncatedTimeStep + [currentTimeStep]

		# make sure the axis are not exactly the same
		minY = min(overAllBandwidth)
		maxY = max(overAllBandwidth)
		if (maxY <= minY):
			maxY = minY + 1.0

		histo[2].set_ylim(bottom= minY * 0.99, top=maxY * 1.2)
		histo[2].set_xlim(left=min(timeStep) * 0.99, right=max(timeStep) * 1.01)
		pl = plt.plot(timeStep, overAllBandwidth, color="green")

		if fit:
			if (len(truncatedTimeStep) == 0) or (len(truncatedOverAllBandwidth)==0):
				print("Skipping fit due to the lack of input data")
				return
			fitRes = numpy.polyfit(truncatedTimeStep, truncatedOverAllBandwidth, 0)

			avgVal = fitRes[0]
			plt.axhline(y=avgVal, xmin=trunctationFractionFront, xmax=1.0 - trunctationFractionBack, color="black",
						lw=2)

			plt.annotate("%.2f" % avgVal + " " + unit, xy=(relTimeSpan * 0.7, avgVal),
						 xytext=(relTimeSpan * 0.75, avgVal * 0.85), backgroundcolor="gray")

		print " done"

	def handleMinMaxTiming(self, jResult, minTime, maxTime, stampStart, stampDone):
		if (minTime is None) or (maxTime is None):
			minTime = jResult[stampStart]
			maxTime = jResult[stampDone]
		else:
			minTime = min(minTime, jResult[stampStart])
			maxTime = max(maxTime, jResult[stampDone])

		return(minTime, maxTime)

	def produceHistogram(self, naming, lambdaExtractor):
		histogram = self.initHistogram(naming[0], naming[1], naming[2])
		self.plotHistogram(histogram, self.jobResult, lambdaExtractor)
		self.finalizeHistogram(histogram)

	def produceOverallGraph(self, naming, timespan, lambdaExtractor, fit=False, unit="MB/s", cumulate=False):
		if (timespan[0] == timespan[1]) or (timespan[0] is None) or (timespan[1] is None):
			print("Skipping plot " + str(naming) + " because no timespan is available")
			return
		histogram = self.initHistogram(naming[0], naming[1], naming[2])
		self.plotOverall(histogram, self.jobResult, timespan,
						 lambdaExtractor, fit, unit, cumulate)
		self.finalizeHistogram(histogram)

	def display(self):
		self.jobResult = []
		print(str(len(self._jobs)) + " job(s) selected for plots")

		# larger default fonts
		matplotlib.rcParams.update({'font.size': 16})

		minSeOutTime = None
		maxSeOutTime = None

		minSeInTime = None
		maxSeInTime = None

		minWrapperTime = None
		maxWrapperTime = None

		minCmsswTime = None
		maxCmsswTime = None

		workdir = os.path.join(self._jobDB._dbPath, "..")
		for j in self._jobs:
			job = self._jobDB.get(j)

			jInfo = JobInfoProcessor().process(os.path.join(workdir, 'output', 'job_%d' % j))
			if (jInfo is None):
				print("Ignoring job")
				continue

			jResult = extractJobTiming(jInfo, self._task)

			self.jobResult = self.jobResult + [jResult]

			(minSeInTime, maxSeInTime) = self.handleMinMaxTiming(jResult, minSeInTime, maxSeInTime,
																 JobResultEnum.TIMESTAMP_SE_IN_START,
																 JobResultEnum.TIMESTAMP_SE_IN_DONE)
			(minSeOutTime, maxSeOutTime) = self.handleMinMaxTiming(jResult, minSeOutTime, maxSeOutTime,
																   JobResultEnum.TIMESTAMP_SE_OUT_START,
																   JobResultEnum.TIMESTAMP_SE_OUT_DONE)
			(minWrapperTime, maxWrapperTime) = self.handleMinMaxTiming(jResult, minWrapperTime, maxWrapperTime,
																	   JobResultEnum.TIMESTAMP_WRAPPER_START,
																	   JobResultEnum.TIMESTAMP_WRAPPER_DONE)
			(minCmsswTime, maxCmsswTime) = self.handleMinMaxTiming(jResult, minCmsswTime, maxCmsswTime,
																   JobResultEnum.TIMESTAMP_CMSSW_CMSRUN1_START,
																   JobResultEnum.TIMESTAMP_CMSSW_CMSRUN1_DONE)

		self.produceHistogram(("payload_runtime", "Payload Runtime (min)", "Count"),
							  lambda x: getPayloadRuntime(x) / 60.0)
		self.produceHistogram(("event_per_job", "Event per Job", "Count"),
							  lambda x: getEventCount(x))
		self.produceHistogram(("event_rate", "Event Rate (Events/min)", "Count"),
							  lambda x: getEventRate(x))
		self.produceHistogram(("se_in_runtime", "SE In Runtime (s)", "Count"),
							  lambda x: getSeInRuntime(x))
		self.produceHistogram(("se_in_size", "SE IN Size (MB)", "Count"),
							  lambda x: getSeInFilesize(x))
		self.produceHistogram(("se_out_runtime", "SE OUT Runtime (s)", "Count"),
							  lambda x: getSeOutRuntime(x))
		self.produceHistogram(("se_out_bandwidth", "SE OUT Bandwidth (MB/s)", "Count"),
							  lambda x: getSeOutBandwidth(x))
		self.produceHistogram(("se_out_size", "SE OUT Size (MB)", "Count"),
							  lambda x: getSeOutFilesize(x))
		self.produceHistogram(("se_out_runtime", "SE Out Runtime (s)", "Count"),
							  lambda x: getSeOutRuntime(x))

		# job active & complete
		self.produceOverallGraph(("job_active_total", "Time (s)", "Jobs Active"),
								 (minWrapperTime, maxWrapperTime),
								 lambda x, i, ip: getJobActiveAtTimeSpan(x, i, ip))
		self.produceOverallGraph(("job_complete_total", "Time (s)", "Jobs Complete"),
								 (minWrapperTime, maxWrapperTime),
								 lambda x, i, ip: getCompleteRateAtTimeSpan(x, i, ip))
		# stage out active & bandwidth
		self.produceOverallGraph(("se_out_bandwidth_total", "Time (s)", "Total SE OUT Bandwidth (MB/s)"),
								 (minSeOutTime, maxSeOutTime),
								 lambda x, i, ip: getSeOutAverageBandwithAtTimeSpan(x, i, ip), fit=True)
		self.produceOverallGraph(("se_out_active_total", "Time (s)", "Active Stageouts"),
								 (minSeOutTime, maxSeOutTime),
								 lambda x, i, ip: getSeOutActiveAtTimeSpan(x, i, ip))
		# stage in active & bandwidth
		self.produceOverallGraph(("se_in_bandwidth_total", "Time (s)", "Total SE IN Bandwidth (MB/s)"),
								 (minSeInTime, maxSeInTime),
								 lambda x, i, ip: getSeInAverageBandwithAtTimeSpan(x, i, ip), fit=True)
		self.produceOverallGraph(("se_in_active_total", "Time (s)", "Active Stageins"),
								 (minSeInTime, maxSeInTime),
								 lambda x, i, ip: getSeInActiveAtTimeSpan(x, i, ip))
		# total stageout size
		self.produceOverallGraph(("se_out_cum_size", "Time (s)", "Stageout Cumulated Size (GB)"),
								 (minSeOutTime, maxSeOutTime),
								 lambda x, i, ip: getSeOutSizeAtTimeSpan(x, i, ip), cumulate=True)
		# total stagein size
		self.produceOverallGraph(("se_in_cum_size", "Time (s)", "Stagein Cumulated Size (GB)"),
								 (minSeInTime, maxSeInTime),
								 lambda x, i, ip: getSeInSizeAtTimeSpan(x, i, ip), cumulate=True)
		# event rate
		if (minCmsswTime is not None) and (maxCmsswTime is not None):
			self.produceOverallGraph(("event_rate_total", "Time (s)", "Event Rate (Events/min)"),
									 (minCmsswTime, maxCmsswTime),
									 lambda x, i, ip: getEventRateAtTimeSpan(x, i, ip))
		else:
			print "Skipping event_rate_total"
