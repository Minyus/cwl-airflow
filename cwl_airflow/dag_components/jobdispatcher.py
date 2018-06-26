import logging
from airflow.models import BaseOperator
from airflow.utils import (apply_defaults)
import sys
import os
import tempfile
from schema_salad.ref_resolver import Loader
from cwltool.pathmapper import adjustDirObjs, visit_class, trim_listing
from cwltool.process import normalizeFilesDirs
from typing import Text
import json
from cwl_airflow.utils.utils import url_shortname
from six.moves import urllib


class JobDispatcher(BaseOperator):

    @apply_defaults
    def __init__(
            self,
            read_file=None,
            branches=4,
            poke_interval=30,
            op_args=None,
            op_kwargs=None,
            *args, **kwargs):

        super(JobDispatcher, self).__init__(*args, **kwargs)

        self.read_file = read_file
        self.op_args = op_args or []
        self.op_kwargs = op_kwargs or {}
        self.poke_interval = poke_interval
        self.branches = branches


    def add_defaults(self, job_order_object):
        for inp in self.dag.cwlwf.tool["inputs"]:
            if "default" in inp and (not job_order_object or url_shortname(inp["id"]) not in job_order_object):
                if not job_order_object:
                    job_order_object = {}
                job_order_object[url_shortname(inp["id"])] = inp["default"]


    def execute(self, context):
        cwl_context = {}
        logging.info(
            '{self.task_id}: Looking for file {self.read_file}'.format(**locals()))

        jobloaderctx = {
            u"path": {u"@type": u"@id"},
            u"location": {u"@type": u"@id"},
            u"format": {u"@type": u"@id"},
            u"id": u"@id"}
        jobloaderctx.update(self.dag.cwlwf.metadata.get("$namespaces", {}))
        loader = Loader(jobloaderctx, fetcher_constructor=None)

        try:
            job_order_object, _ = loader.resolve_ref(ref=self.read_file, base_url=self.dag.default_args["basedir"], checklinks=False)
        except Exception as e:
            logging.error(Text(e))
            sys.exit()

        logging.info('{0}: Resolved job object from file: {1} \n{2}'.format(self.task_id, self.read_file, json.dumps(job_order_object, indent=4)))
        self.add_defaults(job_order_object)
        logging.info('{0}: Defaults added:\n{1}'.format(self.task_id, json.dumps(job_order_object, indent=4)))

        def pathToLoc(p):
            if "location" not in p and "path" in p:
                p["location"] = p["path"]
                del p["path"]

        def addSizes(p):
            if 'location' in p:
                try:
                    p["size"] = os.stat(p["location"][7:]).st_size  # strip off file://
                except OSError:
                    pass
            elif 'contents' in p:
                p["size"] = len(p['contents'])
            else:
                return  # best effort

        visit_class(job_order_object, ("File", "Directory"), pathToLoc)
        visit_class(job_order_object, ("File"), addSizes)
        adjustDirObjs(job_order_object, trim_listing)
        normalizeFilesDirs(job_order_object)

        if "cwl:tool" in job_order_object:
            del job_order_object["cwl:tool"]
        if "id" in job_order_object:
            del job_order_object["id"]

        logging.info('{0}: Job object after adjustment and normalization: \n{1}'.format(self.task_id, json.dumps(job_order_object, indent=4)))

        fragment = urllib.parse.urlsplit(self.dag.default_args["main_workflow"]).fragment
        fragment = fragment + '/' if fragment else ''
        job_order_object_extended = {fragment + key: value for key, value in job_order_object.items()}

        cwl_context['promises'] = job_order_object_extended
        logging.info(
            '{0}: Output: \n {1}'.format(self.task_id, json.dumps(cwl_context, indent=4)))
        return cwl_context
