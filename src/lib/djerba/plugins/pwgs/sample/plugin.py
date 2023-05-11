"""Djerba plugin for pwgs sample reporting"""
import os
import csv
import logging
import json

from mako.lookup import TemplateLookup
from djerba.plugins.base import plugin_base
import djerba.plugins.pwgs.constants as constants
import djerba.plugins.pwgs.analysis.plugin as analysis
from djerba.core.workspace import workspace
import djerba.core.constants as core_constants
from djerba.core.workspace import workspace
from djerba.util.subprocess_runner import subprocess_runner

try:
    import gsiqcetl.column
    from gsiqcetl import QCETLCache
except ImportError:
        raise ImportError('Error Importing QC-ETL, try checking python versions')

class main(plugin_base):

    SNV_COUNT_SUFFIX = 'SNP.count.txt'
    DEFAULT_CONFIG_PRIORITY = 100

    def __init__(self, workspace, identifier, log_level=logging.INFO, log_path=None):
        super().__init__(workspace, identifier, log_level, log_path)
        #self.add_ini_required('primary_snv_count_file')
        self.set_ini_default(core_constants.CLINICAL, True)
        self.set_ini_default(core_constants.SUPPLEMENTARY, False)

    def configure(self, config):
        config = self.apply_defaults(config)
        config = self.set_all_priorities(config, self.DEFAULT_CONFIG_PRIORITY)
        try:
            self.provenance = analysis.main(self.workspace, self.identifier).subset_provenance()
            config[self.identifier][constants.SNV_COUNT_FILE] = analysis.main(self, self.identifier).parse_file_path(self.SNV_COUNT_SUFFIX, self.provenance)
        except OSError:
            self.logger.info("PWGS SAMPLE: Files pulled from ini")
        return config

    def extract(self, config):
        tumour_id = config['core']['tumour_id']
        qc_dict = self.fetch_coverage_etl_data(tumour_id)
        snv_count = self.preprocess_snv_count(config[self.identifier][constants.SNV_COUNT_FILE])
        insert_size_dist_file = self.preprocess_bamqc(config[self.identifier][constants.BAMQC])
        self.plot_insert_size(insert_size_dist_file, 
                             output_dir = self.workspace.print_location())

        self.logger.info("PWGS SAMPLE: All data found")
        data = {
            'plugin_name': self.identifier+' plugin',
            'priorities': self.get_my_priorities(config),
            'attributes': self.get_my_attributes(config),
            'merge_inputs': {
            },
            'results': {
                'median_insert_size': qc_dict['insertSize'],
                'coverage': qc_dict['coverage'],
                'primary_snv_count': snv_count
            }
        }
        return data

    def render(self, data):
        args = data
        html_dir = os.path.realpath(os.path.join(
            os.path.dirname(__file__),
            '..',
            'html'
        ))
        report_lookup = TemplateLookup(directories=[html_dir, ], strict_undefined=True)
        mako_template = report_lookup.get_template(constants.SAMPLE_TEMPLATE_NAME)
        try:
            html = mako_template.render(**args)
        except Exception as err:
            msg = "Unexpected error of type {0} in Mako template rendering: {1}".format(type(err).__name__, err)
            self.logger.error(msg)
            raise
        return html    
    
    def fetch_coverage_etl_data(self,tumour_id):
        self.qcetl_cache = "/scratch2/groups/gsi/production/qcetl_v1"
        self.etl_cache = QCETLCache(self.qcetl_cache)
        cached_coverages = self.etl_cache.bamqc4merged.bamqc4merged
        columns_of_interest = gsiqcetl.column.BamQc4MergedColumn
        data = cached_coverages.loc[
            (cached_coverages[columns_of_interest.GroupID] == tumour_id),
            [columns_of_interest.GroupID, columns_of_interest.CoverageDeduplicated, columns_of_interest.InsertMedian]
            ]
        if len(data) > 0:
           qc_dict = {'coverage' : round(data.iloc[0][columns_of_interest.CoverageDeduplicated].item(),1)}
           qc_dict['insertSize'] = round(data.iloc[0][columns_of_interest.InsertMedian].item(),1)
           return(qc_dict)
        else:
            msg = "Djerba couldn't find the QC metrics associated with tumour_id {0} in QC-ETL. ".format(tumour_id)
            self.logger.debug(msg)
            raise MissingQCETLError(msg)
        
    def preprocess_snv_count(self, snv_count_path):
        """
        pull SNV count from file
        """
        with open(snv_count_path, 'r') as hbc_file:
            reader_file = csv.reader(hbc_file, delimiter="\t")
            for row in reader_file:
                try: 
                    snv_count = row[0]
                except IndexError as err:
                    msg = "Incorrect number of columns in SNV Count file: '{0}'".format(snv_count_path)
                    raise RuntimeError(msg) from err
        return int(snv_count)

    def preprocess_bamqc(self, bamqc_file):
        output_dir = self.workspace.print_location()
        with open(bamqc_file, 'r') as bamqc_results:
            data = json.load(bamqc_results)
        is_data = data['insert size histogram']
        file_location = os.path.join(output_dir, 'insert_size_distribution.csv')
        with open(file_location,'w') as out:
            csv_out = csv.writer(out)
            csv_out.writerow(['size','count'])
            for i in is_data:
                csv_out.writerow([i,is_data[i]])
        return(file_location)

    def plot_insert_size(self, is_path, output_dir ):
        args = [
            os.path.join(constants.RSCRIPTS_LOCATION,'IS.plot.R'),
            '--insert_size_file', is_path,
            '--output_directory', output_dir 
        ]
        subprocess_runner().run(args)


class MissingQCETLError(Exception):
    pass 
