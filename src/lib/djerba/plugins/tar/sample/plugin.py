"""
Djerba plugin for pwgs sample reporting
AUTHOR: Felix Beaudry
"""
import os
import csv
import logging
import json
from decimal import Decimal

from mako.lookup import TemplateLookup
from djerba.plugins.base import plugin_base
import djerba.plugins.tar.sample.constants as constants
from djerba.core.workspace import workspace
import djerba.core.constants as core_constants
from djerba.core.workspace import workspace
from djerba.util.subprocess_runner import subprocess_runner
from djerba.util.render_mako import mako_renderer
import djerba.plugins.tar.provenance_tools as provenance_tools

try:
    import gsiqcetl.column
    from gsiqcetl import QCETLCache
except ImportError:
        raise ImportError('Error Importing QC-ETL, try checking python versions')

class main(plugin_base):

    PLUGIN_VERSION = '1.0.0'
    PRIORITY = 200
    QCETL_CACHE = "/scratch2/groups/gsi/production/qcetl_v1"
    
    # Has the necessary input parameters
    INPUT_PARAMS_FILE = "input_params.json"

    def configure(self, config):
        config = self.apply_defaults(config)
        wrapper = self.get_config_wrapper(config)
        
        # If input_params.json exists, read it
        work_dir = self.workspace.get_work_dir()
        input_data_path = os.path.join(work_dir, self.INPUT_PARAMS_FILE)
        if os.path.exists(input_data_path):
            input_data = self.workspace.read_json(self.INPUT_PARAMS_FILE)
        else:
            msg = "Could not find input_params.json"
            #print(msg) <-- TO DO: have logger raise warning 
            

        # FIRST PASS: Get the input parameters
        if wrapper.my_param_is_null('group_id'):
            wrapper.set_my_param('group_id', input_data['tumour_id'])
        if wrapper.my_param_is_null('normal_id'):
            wrapper.set_my_param('normal_id', input_data['normal_id'])
        if wrapper.my_param_is_null('oncotree_code'):
            wrapper.set_my_param('oncotree_code', input_data['oncotree_code'])
        if wrapper.my_param_is_null('known_variants'):
            wrapper.set_my_param('known_variants', input_data['known_variants'])

        # SECOND PASS: Get files based on input parameters
        if wrapper.my_param_is_null('ichorcna_file'):
            wrapper.set_my_param('ichorcna_file', provenance_tools.subset_provenance_sample(self, "ichorcna", config[self.identifier]['group_id'], "metrics\.json$"))
        if wrapper.my_param_is_null('consensus_cruncher_file'):
            wrapper.set_my_param('consensus_cruncher_file', provenance_tools.subset_provenance_sample(self, "consensusCruncher", config[self.identifier]['group_id'], "allUnique-hsMetrics\.HS\.txt$"))
        if wrapper.my_param_is_null('consensus_cruncher_file_normal'):
            wrapper.set_my_param('consensus_cruncher_file_normal', provenance_tools.subset_provenance_sample(self, "consensusCruncher", config[self.identifier]['normal_id'], "allUnique-hsMetrics\.HS\.txt$"))
        if wrapper.my_param_is_null('raw_coverage'):
            qc_dict = self.fetch_coverage_etl_data(config[self.identifier]['group_id'])
            wrapper.set_my_param('raw_coverage', qc_dict['raw_coverage'])

        # Get values for collapsed coverage for Pl and BC and put in config for QC reporting
        if wrapper.my_param_is_null('collapsed_coverage_pl'):
            wrapper.set_my_param('collapsed_coverage_pl', self.process_consensus_cruncher(config[self.identifier]['consensus_cruncher_file']))
        if wrapper.my_param_is_null('collapsed_coverage_bc'):
            wrapper.set_my_param('collapsed_coverage_bc', self.process_consensus_cruncher(config[self.identifier]['consensus_cruncher_file_normal']))
        
        return wrapper.get_config()
    
    def extract(self, config):
        wrapper = self.get_config_wrapper(config)
        work_dir = self.workspace.get_work_dir()
        data = self.get_starting_plugin_data(wrapper, self.PLUGIN_VERSION)
        work_dir = self.workspace.get_work_dir()

        # Get purity and write it to purity.txt
        ichorcna_metrics_file = config[self.identifier]['ichorcna_file']
        ichor_json = self.process_ichor_json(ichorcna_metrics_file)
        self.workspace.write_json('ichor_metrics.json', ichor_json)
        purity = ichor_json["tumor_fraction"]
        self.write_purity(purity, work_dir)

        results =  {
                "oncotree_code": config[self.identifier]['oncotree_code'],
                "known_variants" : config[self.identifier][constants.KNOWN_VARIANTS],
                "cancer_content" : float('%.1E' % Decimal(purity*100)),
                "raw_coverage" : int(config[self.identifier][constants.RAW_COVERAGE]),
                "unique_coverage" : int(config[self.identifier][constants.COLLAPSED_COVERAGE_PL]),
                "files": {
                    "consensus_cruncher_file": config[self.identifier]['consensus_cruncher_file'],
                    "ichorcna_file": config[self.identifier]['ichorcna_file']
                }
            }
        data['results'] = results
        return data

    def fetch_coverage_etl_data(self, group_id):
        etl_cache = QCETLCache(self.QCETL_CACHE)
        cached_coverages = etl_cache.hsmetrics.metrics
        columns_of_interest = gsiqcetl.column.HsMetricsColumn
        data = cached_coverages.loc[ (cached_coverages[columns_of_interest.GroupID] == group_id),  [columns_of_interest.GroupID, columns_of_interest.MeanBaitCoverage] ]
        qc_dict = {}
        if len(data) > 0:
           qc_dict['raw_coverage'] = int(round(data.iloc[0][columns_of_interest.MeanBaitCoverage].item(),0))
        else:
            msg = "QC metrics associated with group_id {0} not found in QC-ETL and no value found in .ini ".format(group_id)
            raise MissingQCETLError(msg)
        return(qc_dict)

    def render(self, data):
        renderer = mako_renderer(self.get_module_dir())
        return renderer.render_name('sample_template.html', data)

    def process_ichor_json(self, ichor_metrics):
        with open(ichor_metrics, 'r') as ichor_results:
            ichor_json = json.load(ichor_results)
        return(ichor_json)

    def process_consensus_cruncher(self, consensus_cruncher_file):
        header_line = False
        with open(consensus_cruncher_file, 'r') as cc_file:
            reader_file = csv.reader(cc_file, delimiter="\t")
            for row in reader_file:
                if row:
                    if row[0] == "BAIT_SET" :
                        header_line = True
                    elif header_line:
                        unique_coverage = float(row[9]) 
                        header_line = False
                    else:
                        next
        return(int(round(unique_coverage, 0)))

    def specify_params(self):
        discovered = [
            'group_id',
            'normal_id',
            'oncotree_code',
            'known_variants',
            'ichorcna_file',
            'raw_coverage',
            'consensus_cruncher_file',
            'consensus_cruncher_file_normal',
            'collapsed_coverage_pl',
            'collapsed_coverage_bc'
        ]
        for key in discovered:
            self.add_ini_discovered(key)
        self.set_ini_default(core_constants.ATTRIBUTES, 'clinical')
        self.set_priority_defaults(self.PRIORITY)

    def write_purity(self, purity, work_dir):
        """
        Writes the purity to a .txt file for other plugins to read.
        """
        out_path = os.path.join(work_dir, 'purity.txt')
        with open(out_path, "w") as file:
            file.write(str(purity))
        return out_path

class MissingQCETLError(Exception):
    pass 
