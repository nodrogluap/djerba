"""
- Parse contents of the report directory generated by singleSample.r
- Output as JSON; this is a first step towards replacing the directory with a JSON file
- JSON output contains all necessary data for the HTML report (by definition);
but is not compliant with the Elba schema
"""

import csv
import json
import logging
import os
import re
import djerba.util.constants as constants
import djerba.util.ini_fields as ini
from djerba.util.logger import logger

class report_directory_parser(logger):

    # dictionary keys
    HEADER = 'header'
    BODY = 'body'

    # files in report directory
    ANALYSIS_UNIT = constants.ANALYSIS_UNIT_FILENAME
    DATA_CLINICAL = constants.CLINICAL_DATA_FILENAME
    DATA_CNA_ONCOKBGENES_NONDIPLOID_ANNOTATED = 'data_CNA_oncoKBgenes_nonDiploid_annotated.txt'
    DATA_CNA_ONCOKBGENES_NONDIPLOID = 'data_CNA_oncoKBgenes_nonDiploid.txt'
    DATA_CNA = 'data_CNA.txt'
    DATA_EXPRESSION_PERCENTILE_COMPARISON = 'data_expression_percentile_comparison.txt'
    DATA_EXPRESSION_PERCENTILE_TCGA = 'data_expression_percentile_tcga.txt'
    DATA_EXPRESSION_ZSCORES_COMPARISON = 'data_expression_zscores_comparison.txt'
    DATA_EXPRESSION_ZSCORES_TCGA = 'data_expression_zscores_tcga.txt'
    DATA_FUSIONS_ONCOKB_ANNOTATED = 'data_fusions_oncokb_annotated.txt'
    DATA_FUSIONS = 'data_fusions.txt'
    DATA_LOG2CNA = 'data_log2CNA.txt'
    DATA_MUTATIONS_EXTENDED_ONCOGENIC = 'data_mutations_extended_oncogenic.txt'
    DATA_MUTATIONS_EXTENDED = 'data_mutations_extended.txt'
    DATA_SEGMENTS = 'data_segments.txt'
    GENOMIC_SUMMARY = constants.GENOMIC_SUMMARY_FILENAME
    SIGS_WEIGHTS = 'sigs/weights.txt'

    # list of all input files
    ALL_CONTENTS = [
        ANALYSIS_UNIT,
        DATA_CLINICAL, 
        DATA_CNA_ONCOKBGENES_NONDIPLOID_ANNOTATED, 
        DATA_CNA_ONCOKBGENES_NONDIPLOID, 
        DATA_CNA, 
        DATA_EXPRESSION_PERCENTILE_COMPARISON, 
        DATA_EXPRESSION_PERCENTILE_TCGA, 
        DATA_EXPRESSION_ZSCORES_COMPARISON, 
        DATA_EXPRESSION_ZSCORES_TCGA,
        DATA_FUSIONS_ONCOKB_ANNOTATED, 
        DATA_FUSIONS, 
        DATA_LOG2CNA, 
        DATA_MUTATIONS_EXTENDED_ONCOGENIC, 
        DATA_MUTATIONS_EXTENDED, 
        DATA_SEGMENTS, 
        GENOMIC_SUMMARY, 
        SIGS_WEIGHTS
    ]

    # subsets of input files, to determine the reading mode
    FLOAT_INPUTS = [
        DATA_EXPRESSION_PERCENTILE_TCGA,
        DATA_EXPRESSION_ZSCORES_TCGA,
        DATA_LOG2CNA
    ]
    FLOAT_LIST_INPUTS = [
        DATA_EXPRESSION_PERCENTILE_COMPARISON,
        DATA_EXPRESSION_ZSCORES_COMPARISON
    ]
    INTEGER_INPUTS = [
        DATA_CNA_ONCOKBGENES_NONDIPLOID,
        DATA_CNA
    ]
    FLOAT_LIST_DICT_INPUTS = [
        DATA_EXPRESSION_PERCENTILE_COMPARISON,
        SIGS_WEIGHTS
    ]

    # data reader modes
    TSV_MODE = 0
    INTEGER_DICT_MODE = 1
    FLOAT_DICT_MODE = 2
    FLOAT_LIST_DICT_MODE = 3
    SEGMENTS_MODE = 4
    
    def __init__(self, report_dir, log_level=logging.WARN, log_path=None):
        """
        Read each file in the reporting directory to an appropriate data structure.
        Column headers (if any) are recorded in a separate list.
        Body of the file may be:
        - Dictionary of integers/floats
        - Array of arrays of strings
        - Custom
        """
        self.report_dir = report_dir
        self.logger = self.get_logger(log_level, __name__, log_path)
        for filename in self.ALL_CONTENTS:
            if not os.path.exists(os.path.join(self.report_dir, filename)):
                msg = "Reporting file {0} not found in {1}".format(filename, self.report_dir)
                self.logger.error(msg)
                raise OSError(msg)
        total = len(self.ALL_CONTENTS)
        msg = "{0} required files found in reporting directory {1}".format(total, self.report_dir)
        self.logger.debug(msg)
        self.summary = {}
        for filename in self.ALL_CONTENTS:
            in_path = os.path.join(report_dir, filename)
            key = re.split('\.[A-Za-z]+$', filename).pop(0)
            if filename == self.ANALYSIS_UNIT:
                self.summary[key] = self.read_analysis_unit(in_path)
            elif filename == self.DATA_CLINICAL:
                self.summary[key] = self.read_clinical_data(in_path)
            elif filename == self.GENOMIC_SUMMARY:
                self.summary[key] = self.read_genomic_summary(in_path)
            elif filename == self.DATA_SEGMENTS:
                self.summary[key] = self.read_data_file(in_path, self.SEGMENTS_MODE)
            elif filename in self.INTEGER_INPUTS:
                self.summary[key] = self.read_data_file(in_path, self.INTEGER_DICT_MODE)
            elif filename in self.FLOAT_INPUTS:
                self.summary[key] = self.read_data_file(in_path, self.FLOAT_DICT_MODE)
            elif filename in self.FLOAT_LIST_DICT_INPUTS:
                self.summary[key] = self.read_data_file(in_path, self.FLOAT_LIST_DICT_MODE)
            else:
                self.summary[key] = self.read_data_file(in_path, self.TSV_MODE)
            self.logger.debug("Read data from {0}".format(in_path))

    def get_summary(self):
        return self.summary

    def read_analysis_unit(self, in_path):
        """
        Read the analysis unit
        Record a dummy header for consistency with other inputs
        """
        with open(in_path) as in_file:
            analysis_unit = in_file.read().strip()
        header = [ini.ANALYSIS_UNIT]
        body = {ini.ANALYSIS_UNIT: analysis_unit}
        data = {
            self.HEADER: header,
            self.BODY: body
        }
        self.logger.debug("Read analysis_unit from {0}".format(in_path))
        return data


    def read_clinical_data(self, in_path):
        """
        Read the data_clinical.txt file
        Record a dummy header for consistency with other inputs
        """
        with open(in_path) as in_file:
            reader = csv.reader(in_file, delimiter="\t")
            keys = next(reader)
            values = next(reader)
        if len(keys)!=len(values):
            msg = "Mismatched keys/values lengths in clinical data file {0}".format(in_path)
            self.logger.error(msg)
            raise ValueError(msg)
        data = {keys[i]:values[i] for i in range(len(keys))}
        self.logger.debug("Read clinical data: {}".format(data))
        # convert the numeric data types
        # column headers in data_clinical.txt are upper-case, Djerba constants are lower-case
        # inconsistent, but kept for compatibility with html_report.Rmd
        keys = [
            ini.MEAN_COVERAGE,
            ini.PCT_V7_ABOVE_80X,
            constants.SEQUENZA_PLOIDY_KEY,
            constants.SEQUENZA_PURITY_KEY
        ]
        for key in keys:
            data[key] = float(data[key.upper()])
        # create the output structure
        header = ["data_clinical"]
        data = {
            self.HEADER: header,
            self.BODY: data
        }
        self.logger.debug("Read clinical data from {0}".format(in_path))
        return data

    def read_data_file(self, in_path, mode):
        """
        Read a TSV file into a data structure with head/body elements
        Header is a list of strings
        Structure of body is determined by mode
        """
        body = []
        with open(in_path) as in_file:
            reader = csv.reader(in_file, delimiter="\t")
            header = next(reader)
            if mode == self.TSV_MODE:
                body = [x for x in reader]
            elif mode == self.INTEGER_DICT_MODE:
                body = {row[0]: int(row[1]) for row in reader}
            elif mode == self.FLOAT_DICT_MODE:
                body = {row[0]: float(row[1]) for row in reader}
            elif mode == self.FLOAT_LIST_DICT_MODE:
                body = {row[0]: [float(x) for x in row[1:]] for row in reader}
            elif mode == self.SEGMENTS_MODE:
                body = {}
                for row in reader:
                    body[row[0]] = [row[1], int(row[2]), int(row[3]), int(row[4]), float(row[5])]
            else:
                msg = "Unknown reader mode '{0}'".format(mode)
                self.logger.error(msg)
                raise ValueError(msg)
        data = {
            self.HEADER: header,
            self.BODY: body
        }
        return data

    def read_genomic_summary(self, in_path):
        """
        Slurp the genomic summary into a single string
        Include a dummy header, for consistency with other methods
        """
        header = ["genomic_summary"]
        with open(in_path) as in_file:
            body = in_file.read()
        data = {
            self.HEADER: header,
            self.BODY: body
        }
        return data

    def write_json(self, out_path):
        with open(out_path, 'w') as out_file:
            out_file.write(json.dumps(self.summary, sort_keys='true'))
        self.logger.debug("Wrote JSON summary to {0}".format(out_path))
