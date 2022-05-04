#! /usr/bin/env python3

"""Read a Djerba 'report' directory and generate JSON for the Mako template"""

import base64
import constants
import csv
import json
import logging
import os
import re
import sys # TODO remove along with main() method
import pandas as pd
import djerba.util.constants as djerba_constants
from djerba.util.logger import logger
from djerba.util.subprocess_runner import subprocess_runner
from statsmodels.distributions.empirical_distribution import ECDF

class composer_base(logger):
    # base class with shared methods and constants

    FDA_APPROVED_LEVELS = ['LEVEL_1', 'LEVEL_2', 'LEVEL_R1']
    INVESTIGATIONAL_LEVELS = ['LEVEL_3A', 'LEVEL_3B', 'LEVEL_4', 'LEVEL_R2']
    NA = 'NA'
    ONCOGENIC = 'oncogenic'
    THERAPY_LEVELS = [
        'LEVEL_1',
        'LEVEL_2',
        'LEVEL_3A',
        'LEVEL_3B',
        'LEVEL_4',
        'LEVEL_R1',
        'LEVEL_R2',
    ]

    def is_null_string(self, value):
        if isinstance(value, str):
            return value in ['', self.NA]
        else:
            msg = "Invalid argument to is_null_string(): '{0}' of type '{1}'".format(value, type(value))
            raise RuntimeError(msg)

    def parse_max_oncokb_level_and_therapies(self, row_dict, levels):
        # find maximum level (if any) from given levels list, and associated therapies
        max_level = None
        therapies = []
        for level in levels:
            if not self.is_null_string(row_dict[level]):
                if not max_level: max_level = level
                therapies.append(row_dict[level])
        if max_level:
            max_level = self.reformat_level_string(max_level)
        # insert a space between comma and start of next word
        therapies = [re.sub(r'(?<=[,])(?=[^\s])', r' ', t) for t in therapies]
        return (max_level, '; '.join(therapies))

    def parse_oncokb_level(self, row_dict):
        # find oncokb level string: eg. "Level 1", "Likely Oncogenic", "None"
        max_level = None
        for level in self.THERAPY_LEVELS:
            if not self.is_null_string(row_dict[level]):
                max_level = level
                break
        if max_level:
            parsed_level = self.reformat_level_string(max_level)
        elif not self.is_null_string(row_dict[self.ONCOGENIC]):
            parsed_level = row_dict[self.ONCOGENIC]
        else:
            parsed_level = self.NA
        return parsed_level

    def reformat_level_string(self, level):
        return re.sub('LEVEL_', 'Level ', level)

class clinical_report_json_composer(composer_base):

    ALTERATION_UPPER_CASE = 'ALTERATION'
    CANCER_TYPE_HEADER = 'CANCER.TYPE' # for tmbcomp files
    COMPASS = 'COMPASS'
    GENOME_SIZE = 3*10**9 # TODO use more accurate value when we release a new report format
    HGVSP_SHORT = 'HGVSp_Short'
    HUGO_SYMBOL_TITLE_CASE = 'Hugo_Symbol'
    HUGO_SYMBOL_UPPER_CASE = 'HUGO_SYMBOL'
    MINIMUM_MAGNITUDE_SEG_MEAN = 0.2
    MUTATIONS_EXTENDED_ONCOGENIC = 'data_mutations_extended_oncogenic.txt'
    MUTATIONS_EXTENDED = 'data_mutations_extended.txt'
    CNA_ANNOTATED = 'data_CNA_oncoKBgenes_nonDiploid_annotated.txt'
    INTRAGENIC = 'intragenic'
    ONCOKB_URL_BASE = 'https://www.oncokb.org/gene'
    FDA_APPROVED = 'FDA_APPROVED'
    INVESTIGATIONAL = 'INVESTIGATIONAL'
    PAN_CANCER_COHORT = 'TCGA Pan-Cancer Atlas 2018 (n=6,446)'
    PLACEHOLDER = "<strong style='color:red;'>*** PLACEHOLDER ***</strong>"
    TMB_HEADER = 'tmb' # for tmbcomp files
    TMBCOMP_EXTERNAL = 'tmbcomp-externaldata.txt'
    TMBCOMP_TCGA = 'tmbcomp-tcga.txt'
    TUMOUR_VAF = 'tumour_vaf'
    UNKNOWN = 'Unknown'
    VARIANT_CLASS = 'Variant_Classification'
    V7_TARGET_SIZE = 37.285536 # inherited from CGI-Tools

    ALL_ONCOKB_OUTPUT_LEVELS = [
        'Level 1',
        'Level 2',
        'Level 3A',
        'Level 3B',
        'Level 4',
        'Level R1',
        'Level R2',
        'Likely Oncogenic'
    ]

    def __init__(self, input_dir, author, assay_type, coverage=80, failed=False, purity_failure=False,
                 log_level=logging.WARNING, log_path=None):
        self.log_level = log_level
        self.log_path = log_path
        self.logger = self.get_logger(log_level, __name__, log_path)
        self.input_dir = input_dir
        self.all_reported_variants = set()
        self.assay_type = assay_type
        self.author = author
        self.coverage = coverage
        self.failed = failed
        self.purity_failure = purity_failure
        self.clinical_data = self.read_clinical_data()
        self.closest_tcga_lc = self.clinical_data['CLOSEST_TCGA'].lower()
        self.closest_tcga_uc = self.clinical_data['CLOSEST_TCGA'].upper()
        self.data_dir = os.path.join(os.environ['DJERBA_BASE_DIR'], djerba_constants.DATA_DIR_NAME)
        self.r_script_dir = os.path.join(os.environ['DJERBA_BASE_DIR'], 'R_plots')
        self.html_dir = os.path.join(os.environ['DJERBA_BASE_DIR'], 'template', 'html')
        self.cytoband_map = self.read_cytoband_map()
        self.total_somatic_mutations = self.read_total_somatic_mutations()
        self.total_oncogenic_somatic_mutations = self.read_total_oncogenic_somatic_mutations()
        fus_reader = fusion_reader(input_dir, log_level=log_level, log_path=log_path)
        self.total_fusion_genes = fus_reader.get_total_fusion_genes()
        self.gene_pair_fusions = fus_reader.get_fusions()

    def build_alteration_url(self, gene, alteration, cancer_code):
        return '/'.join([self.ONCOKB_URL_BASE, gene, alteration, cancer_code])

    def build_copy_number_variation(self):
        [oncogenic_cnv_total, cnv_total, oncogenic_variants] = self.read_cnv_data()
        data = {
            constants.TOTAL_VARIANTS: cnv_total,
            constants.CLINICALLY_RELEVANT_VARIANTS: oncogenic_cnv_total,
            constants.BODY: self.sort_by_oncokb_level(oncogenic_variants)
        }
        return data

    def build_coverage_thresholds(self):
        coverage_thresholds = {
            constants.NORMAL_MIN: 30,
            constants.NORMAL_TARGET: 40
        }
        if self.coverage == 40:
            coverage_thresholds[constants.TUMOUR_MIN] = 40
            coverage_thresholds[constants.TUMOUR_TARGET] = 50
        elif self.coverage == 80:
            coverage_thresholds[constants.TUMOUR_MIN] = 80
            coverage_thresholds[constants.TUMOUR_TARGET] = 100
        else:
            raise RuntimeError("Unknown depth of coverage")
        return coverage_thresholds

    def build_fda_approved_info(self):
        return self.build_therapy_info(self.FDA_APPROVED)

    def build_gene_url(self, gene):
        return '/'.join([self.ONCOKB_URL_BASE, gene])

    def build_genomic_landscape_info(self):
        # need to calculate TMB and percentiles
        cohort = self.read_cohort()
        data = {}
        data[constants.TMB_TOTAL] = self.total_somatic_mutations
        # TODO See GCGI-347 for possible updates to V7_TARGET_SIZE
        data[constants.TMB_PER_MB] = round(self.total_somatic_mutations/self.V7_TARGET_SIZE, 2)
        data[constants.PERCENT_GENOME_ALTERED] = int(round(self.read_fga()*100, 0))
        csp = self.read_cancer_specific_percentile(data[constants.TMB_PER_MB], cohort, self.closest_tcga_lc)
        data[constants.CANCER_SPECIFIC_PERCENTILE] = int(round(csp, 0))
        data[constants.CANCER_SPECIFIC_COHORT] = cohort
        pcp = self.read_pan_cancer_percentile(data[constants.TMB_PER_MB])
        data[constants.PAN_CANCER_PERCENTILE] = int(round(pcp, 0))
        data[constants.PAN_CANCER_COHORT] = self.PAN_CANCER_COHORT
        return data

    def build_investigational_therapy_info(self):
        return self.build_therapy_info(self.INVESTIGATIONAL)

    def build_patient_info(self):
        # TODO import clinical data column names from Djerba constants module
        data = {}
        tumour_id = self.clinical_data['TUMOUR_SAMPLE_ID']
        data[constants.ASSAY] = self.PLACEHOLDER
        data[constants.BLOOD_SAMPLE_ID] = self.clinical_data['BLOOD_SAMPLE_ID']
        data[constants.SEX] = self.clinical_data['SEX']
        data[constants.PATIENT_LIMS_ID] = self.clinical_data['PATIENT_LIMS_ID']
        data[constants.PATIENT_STUDY_ID] = self.clinical_data['PATIENT_STUDY_ID']
        data[constants.PRIMARY_CANCER] = self.clinical_data['CANCER_TYPE_DESCRIPTION']
        data[constants.REPORT_ID] = "{0}-v{1}".format(tumour_id, self.clinical_data['REPORT_VERSION'])
        data[constants.REQ_APPROVED_DATE] = self.clinical_data['REQ_APPROVED_DATE']
        data[constants.SITE_OF_BIOPSY_OR_SURGERY] = self.clinical_data['SAMPLE_ANATOMICAL_SITE']
        data[constants.STUDY] = self.PLACEHOLDER
        data[constants.TUMOUR_SAMPLE_ID] = tumour_id
        return data

    def build_sample_info(self):
        data = {}
        data[constants.CALLABILITY_PERCENT] = float(self.clinical_data['PCT_V7_ABOVE_80X'])
        data[constants.COVERAGE_MEAN] = float(self.clinical_data['MEAN_COVERAGE'])
        data[constants.PLOIDY] = float(self.clinical_data['SEQUENZA_PLOIDY'])
        data[constants.PURITY_PERCENT] = float(self.clinical_data['SEQUENZA_PURITY_FRACTION'])
        data[constants.ONCOTREE_CODE] = self.PLACEHOLDER
        data[constants.SAMPLE_TYPE] = self.clinical_data['SAMPLE_TYPE']
        return data

    def build_small_mutations_and_indels(self):
        # read in small mutations; output rows for oncogenic mutations
        rows = []
        mutation_copy_states = self.read_mutation_copy_states()
        with open(os.path.join(self.input_dir, self.MUTATIONS_EXTENDED_ONCOGENIC)) as data_file:
            for input_row in csv.DictReader(data_file, delimiter="\t"):
                gene = input_row[self.HUGO_SYMBOL_TITLE_CASE]
                cytoband = self.get_cytoband(gene)
                self.all_reported_variants.add((gene, cytoband))
                protein = input_row[self.HGVSP_SHORT]
                row = {
                    constants.GENE: gene,
                    constants.GENE_URL: self.build_gene_url(gene),
                    constants.CHROMOSOME: cytoband,
                    constants.PROTEIN: protein,
                    constants.PROTEIN_URL: self.build_alteration_url(gene, protein, self.closest_tcga_uc),
                    constants.MUTATION_TYPE: re.sub('_', ' ', input_row[self.VARIANT_CLASS]),
                    constants.VAF_PERCENT: round(float(input_row[self.TUMOUR_VAF]), 2),
                    constants.TUMOUR_DEPTH: int(input_row[constants.TUMOUR_DEPTH]),
                    constants.TUMOUR_ALT_COUNT: int(input_row[constants.TUMOUR_ALT_COUNT]),
                    constants.COPY_STATE: mutation_copy_states[gene],
                    constants.ONCOKB: self.parse_oncokb_level(input_row)
                }
                rows.append(row)
        data = {
            constants.CLINICALLY_RELEVANT_VARIANTS: self.total_oncogenic_somatic_mutations,
            constants.TOTAL_VARIANTS: self.total_somatic_mutations,
            constants.BODY: self.sort_by_oncokb_level(rows)
        }
        return data

    def build_structural_variants_and_fusions(self):
        # table has 2 rows for each oncogenic fusion
        rows = []
        oncogenic_fusion_genes = 0 # number of genes = 2x number of fusions
        for fusion in self.gene_pair_fusions:
            oncokb_level = fusion.get_oncokb_level()
            if oncokb_level == self.UNKNOWN or self.is_null_string(oncokb_level):
                continue # skip non-oncogenic fusions
            oncogenic_fusion_genes += 2
            for gene in fusion.get_genes():
                cytoband = self.get_cytoband(gene)
                self.all_reported_variants.add((gene, cytoband))
                row =  {
                    constants.GENE: gene,
                    constants.GENE_URL: self.build_gene_url(gene),
                    constants.CHROMOSOME: cytoband,
                    constants.FRAME: fusion.get_frame(),
                    constants.FUSION: fusion.get_fusion_id_new(),
                    constants.MUTATION_EFFECT: fusion.get_mutation_effect(),
                    constants.ONCOKB: oncokb_level
                }
                rows.append(row)
        data = {
            constants.CLINICALLY_RELEVANT_VARIANTS: oncogenic_fusion_genes,
            constants.TOTAL_VARIANTS: self.total_fusion_genes,
            constants.BODY: self.sort_by_oncokb_level(rows)
        }
        return data

    def build_supplementary_info(self):
        variants = sorted(list(self.all_reported_variants))
        gene_summaries = self.read_oncokb_gene_summaries()
        rows = []
        for [gene, cytoband] in variants:
            row = {
                constants.GENE: gene,
                constants.GENE_URL: self.build_gene_url(gene),
                constants.CHROMOSOME: cytoband,
                constants.SUMMARY: gene_summaries.get(gene, 'OncoKB summary not available')
            }
            rows.append(row)
        return rows

    def build_therapy_info(self, level):
        # build the "FDA approved" and "investigational" therapies data
        # defined respectively as OncoKB levels 1/2/R1 and R2/3A/3B/4
        # OncoKB "LEVEL" columns contain treatment if there is one, 'NA' otherwise
        # Output columns:
        # - the gene name, with oncoKB link (or pair of names/links, for fusions)
        # - Alteration name, eg. HGVSp_Short value, with oncoKB link
        # - Treatment
        # - OncoKB level
        if level == self.FDA_APPROVED:
            levels = self.FDA_APPROVED_LEVELS
        elif level == self.INVESTIGATIONAL:
            levels = self.INVESTIGATIONAL_LEVELS
        else:
            raise RuntimeError("Unknown therapy level: '{0}'".format(level))
        rows = []
        with open(os.path.join(self.input_dir, self.MUTATIONS_EXTENDED_ONCOGENIC)) as data_file:
            for row in csv.DictReader(data_file, delimiter="\t"):
                gene = row[self.HUGO_SYMBOL_TITLE_CASE]
                alteration = row[self.HGVSP_SHORT]
                [max_level, therapies] = self.parse_max_oncokb_level_and_therapies(row, levels)
                if max_level:
                    rows.append(self.treatment_row(gene, alteration, max_level, therapies))
        with open(os.path.join(self.input_dir, self.CNA_ANNOTATED)) as data_file:
            for row in csv.DictReader(data_file, delimiter="\t"):
                gene = row[self.HUGO_SYMBOL_UPPER_CASE]
                alteration = row[self.ALTERATION_UPPER_CASE]
                [max_level, therapies] = self.parse_max_oncokb_level_and_therapies(row, levels)
                if max_level:
                    rows.append(self.treatment_row(gene, alteration, max_level, therapies))
        for fusion in self.gene_pair_fusions:
            genes = fusion.get_genes()
            alteration = constants.FUSION
            if level == self.FDA_APPROVED:
                max_level = fusion.get_fda_level()
                therapies = fusion.get_fda_therapies()
            else:
                max_level = fusion.get_inv_level()
                therapies = fusion.get_inv_therapies()
            if max_level:
                rows.append(self.treatment_row(genes, alteration, max_level, therapies))
        rows = self.sort_by_oncokb_level(rows)
        return rows

    def build_json(self, out_dir):
        # build the main JSON data structure
        data = {}
        data[constants.ASSAY_TYPE] = self.assay_type
        data[constants.AUTHOR] = self.author
        data[constants.OICR_LOGO] = os.path.join(self.html_dir, 'OICR_Logo_RGB_ENGLISH.png')
        data[constants.PATIENT_INFO] = self.build_patient_info()
        data[constants.SAMPLE_INFO] = self.build_sample_info()
        data[constants.GENOMIC_SUMMARY] = self.read_genomic_summary()
        data[constants.COVERAGE_THRESHOLDS] = self.build_coverage_thresholds()
        data[constants.GENOMIC_LANDSCAPE_INFO] = self.build_genomic_landscape_info()
        tmb = data[constants.GENOMIC_LANDSCAPE_INFO][constants.TMB_PER_MB]
        data[constants.TMB_PLOT] = self.write_tmb_plot(tmb, out_dir)
        data[constants.VAF_PLOT] = self.write_vaf_plot(out_dir)
        data[constants.APPROVED_BIOMARKERS] = self.build_fda_approved_info()
        data[constants.INVESTIGATIONAL_THERAPIES] = self.build_investigational_therapy_info()
        data[constants.SMALL_MUTATIONS_AND_INDELS] = self.build_small_mutations_and_indels()
        data[constants.TOP_ONCOGENIC_SOMATIC_CNVS] = self.build_copy_number_variation()
        data[constants.STRUCTURAL_VARIANTS_AND_FUSIONS] = self.build_structural_variants_and_fusions()
        data[constants.SUPPLEMENTARY_INFO] = self.build_supplementary_info()
        data[constants.FAILED] = self.failed
        data[constants.PURITY_FAILURE] = self.purity_failure
        data[constants.REPORT_DATE] = None
        self.logger.info("Finished building clinical report data structure for JSON output")
        return data

    def get_cytoband(self, gene_name):
        cytoband = self.cytoband_map.get(gene_name)
        if not cytoband:
            cytoband = 'Unknown'
            self.logger.warn("Unknown cytoband for gene '{0}'".format(gene_name))
        return cytoband

    def image_to_json_string(self, image_path, image_type='jpeg'):
        # read a jpeg file into base64 with JSON prefix
        if image_type not in ['jpg', 'jpeg', 'png']:
            raise RuntimeError("Unsupported image type: {0}".format(image_type))
        with open(image_path, 'rb') as image_file:
            image = base64.b64encode(image_file.read())
        image_json = 'data:image/{0};base64,{1}'.format(image_type, image.decode('utf-8'))
        return image_json

    def read_cancer_specific_percentile(self, tmb, cohort, cancer_type):
        # Read percentile for given TMB/Mb and cohort
        # We use statsmodels to compute the ECDF
        # See: https://stackoverflow.com/a/15792672
        # Introduces dependency on Pandas, but still the most convenient solution
        if cohort == self.NA:
            percentile = self.NA
        else:
            if cohort == self.COMPASS:
                data_filename = self.TMBCOMP_EXTERNAL
            else:
                data_filename = self.TMBCOMP_TCGA
            tmb_array = []
            with open(os.path.join(self.data_dir, data_filename)) as data_file:
                for row in csv.DictReader(data_file, delimiter="\t"):
                    if row[self.CANCER_TYPE_HEADER] == cancer_type:
                        tmb_array.append(float(row[self.TMB_HEADER]))
            ecdf = ECDF(tmb_array)
            percentile = ecdf(tmb)*100
        return percentile

    def read_cohort(self):
        # cohort is:
        # 1) COMPASS if 'closest TCGA' is paad
        # 2) CANCER.TYPE from tmbcomp-tcga.txt if one matches 'closest TCGA'
        # 3) NA otherwise
        #
        # Note: cohort in case (1) is really the Source column in tmbcomp-externaldata.txt
        # but for now this only has one value
        # TODO need to define a procedure for adding more data cohorts
        tcga_cancer_types = set()
        with open(os.path.join(self.data_dir, self.TMBCOMP_TCGA)) as tcga_file:
            reader = csv.reader(tcga_file, delimiter="\t")
            for row in reader:
                tcga_cancer_types.add(row[3])
        if self.closest_tcga_lc == 'paad':
            cohort = self.COMPASS
        elif self.closest_tcga in tcga_cancer_types:
            cohort = closest_tcga
        else:
            cohort = self.NA
        return cohort

    def read_clinical_data(self):
        input_path = os.path.join(self.input_dir, 'data_clinical.txt')
        with open(input_path) as input_file:
            reader = csv.reader(input_file, delimiter="\t")
            header = next(reader)
            body = next(reader)
        if len(header)!=len(body):
            raise ValueError("Clinical data header and body of unequal length")
        clinical_data = {}
        for i in range(len(header)):
            clinical_data[header[i]] = body[i]
        return clinical_data

    def read_cnv_data(self):
        input_path = os.path.join(self.input_dir, 'data_CNA_oncoKBgenes_nonDiploid_annotated.txt')
        oncogenic = 0
        total = 0
        oncogenic_variants = []
        with open(input_path) as input_file:
            reader = csv.DictReader(input_file, delimiter="\t")
            for row in reader:
                total += 1
                level = self.parse_oncokb_level(row)
                if level == self.UNKNOWN or self.is_null_string(level):
                    continue
                else:
                    oncogenic += 1
                    gene = row[self.HUGO_SYMBOL_UPPER_CASE]
                    cytoband = self.get_cytoband(gene)
                    self.all_reported_variants.add((gene, cytoband))
                    variant = {
                        constants.GENE: gene,
                        constants.GENE_URL: self.build_gene_url(gene),
                        constants.ALT: row[self.ALTERATION_UPPER_CASE],
                        constants.CHROMOSOME: cytoband,
                        constants.ONCOKB: level
                    }
                    oncogenic_variants.append(variant)
        return [oncogenic, total, oncogenic_variants]

    def read_cytoband_map(self):
        input_path = os.path.join(self.data_dir, 'cytoBand.txt')
        cytobands = {}
        with open(input_path) as input_file:
            reader = csv.DictReader(input_file, delimiter="\t")
            for row in reader:
                cytobands[row[self.HUGO_SYMBOL_TITLE_CASE]] = row['Chromosome']
        return cytobands

    def read_fga(self):
        input_path = os.path.join(self.input_dir, 'data_segments.txt')
        total = 0
        with open(input_path) as input_file:
            for row in csv.DictReader(input_file, delimiter="\t"):
                if abs(float(row['seg.mean'])) >= self.MINIMUM_MAGNITUDE_SEG_MEAN:
                    total += int(row['loc.end']) - int(row['loc.start'])
        # TODO see GCGI-347 for possible updates to genome size
        fga = float(total)/self.GENOME_SIZE
        return fga

    def read_genomic_summary(self):
        with open(os.path.join(self.input_dir, 'genomic_summary.txt')) as in_file:
            return in_file.read().strip()

    def read_mutation_copy_states(self):
        # convert copy state to human readable string; return mapping of gene -> copy state
        copy_state_conversion = {
            0: "Neutral",
            1: "Gain",
            2: "Amplification",
            -1: "Shallow Deletion",
            -2: "Deep Depetion"
        }
        copy_states = {}
        with open(os.path.join(self.input_dir, 'data_CNA.txt')) as in_file:
            first = True
            for row in csv.reader(in_file, delimiter="\t"):
                if first:
                    first = False
                else:
                    [gene, category] = [row[0], int(row[1])]
                    copy_states[gene] = copy_state_conversion.get(category, self.UNKNOWN)
        return copy_states

    def read_oncokb_gene_summaries(self):
        summaries = {}
        with open(os.path.join(self.data_dir, '20201126-allCuratedGenes.tsv')) as in_file:
            for row in csv.DictReader(in_file, delimiter="\t"):
                summaries[row['hugoSymbol']] = row['summary']
        return summaries

    def read_pan_cancer_percentile(self, tmb):
        tmb_array = []
        with open(os.path.join(self.data_dir, self.TMBCOMP_TCGA)) as data_file:
            for row in csv.DictReader(data_file, delimiter="\t"):
                tmb_array.append(float(row[self.TMB_HEADER]))
        ecdf = ECDF(tmb_array)
        percentile = ecdf(tmb)*100
        return percentile

    def read_total_fusions(self):
        return self.read_variant_count(self.DATA_FUSIONS_OLD)

    def read_total_oncogenic_somatic_mutations(self):
        return self.read_variant_count(self.MUTATIONS_EXTENDED_ONCOGENIC)

    def read_total_somatic_mutations(self):
        return self.read_variant_count(self.MUTATIONS_EXTENDED)

    def read_variant_count(self, filename):
        with open(os.path.join(self.input_dir, filename)) as var_file:
            variant_count = len(var_file.readlines()) - 1 # lines in file, minus header line
        return variant_count

    def run(self, out_dir):
        # main method to generate and write JSON
        # TODO finer control of output paths; may wish to write TMB/VAF plots to a tempdir
        out_dir = os.path.realpath(out_dir)
        self.logger.info("Building clinical report data with output to {0}".format(out_dir))
        data = self.build_json(out_dir)
        human_path = os.path.join(out_dir, 'djerba_report_human.json')
        machine_path = os.path.join(out_dir, 'djerba_report_machine.json')
        self.write_human_readable(data, human_path)
        self.logger.info("Wrote human-readable JSON output to {0}".format(human_path))
        self.write_machine_readable(data, machine_path)
        self.logger.info("Wrote machine-readable JSON output to {0}".format(machine_path))
        self.logger.info("Finished.")

    def sort_by_oncokb_level(self, rows):
        # sort table rows from highest to lowest oncoKB level
        def oncokb_order(level):
            # find numeric sort order for an oncokb level; error if level is unknown
            order = -1
            for output_level in self.ALL_ONCOKB_OUTPUT_LEVELS:
                order += 1
                if level == output_level:
                    break
            if order == -1:
                raise RuntimeError("Unknown OncoKB level '{0}'".format(level))
            return order
        try:
            sorted_rows = sorted(rows, key=lambda row: oncokb_order(row[constants.ONCOKB]))
        except RuntimeError as err:
            self.logger.error("Error in OncoKB level sort: {0}".format(err))
            raise
        return sorted_rows

    def treatment_row(self, genes_arg, alteration, max_level, therapies):
        # genes argument may be a string, or an iterable of strings
        if isinstance(genes_arg, str):
            genes_and_urls = {genes_arg: self.build_gene_url(genes_arg)}
        else:
            genes_and_urls = {gene: self.build_gene_url(gene) for gene in genes_arg}
        if alteration == constants.FUSION:
            alt_url = self.build_alteration_url('-'.join(genes_arg), alteration, self.closest_tcga_uc)
        else:
            alt_url = self.build_alteration_url(genes_arg, alteration, self.closest_tcga_uc)
        row = {
            constants.GENES_AND_URLS: genes_and_urls,
            constants.ALT: alteration,
            constants.ALT_URL: alt_url,
            constants.ONCOKB: max_level,
            constants.TREATMENT: therapies
        }
        return row

    def write_human_readable(self, data, out_path):
        # write pretty-printed JSON with file paths
        data[constants.OICR_LOGO] = os.path.abspath(data[constants.OICR_LOGO])
        data[constants.TMB_PLOT] = os.path.abspath(data[constants.TMB_PLOT])
        data[constants.VAF_PLOT] = os.path.abspath(data[constants.VAF_PLOT])
        with open(out_path, 'w') as out_file:
             print(json.dumps(data, sort_keys=True, indent=4), file=out_file)

    def write_machine_readable(self, data, out_path):
        # read in JPEGs as base-64 blobs to make a self-contained document
        data[constants.OICR_LOGO] = self.image_to_json_string(data[constants.OICR_LOGO], 'png')
        data[constants.TMB_PLOT] = self.image_to_json_string(data[constants.TMB_PLOT])
        data[constants.VAF_PLOT] = self.image_to_json_string(data[constants.VAF_PLOT])
        with open(out_path, 'w') as out_file:
             print(json.dumps(data), file=out_file)

    def write_tmb_plot(self, tmb, out_dir):
        out_path = os.path.join(out_dir, 'tmb.jpeg')
        args = [
            os.path.join(self.r_script_dir, 'tmb_plot.R'),
            '-c', self.closest_tcga_lc,
            '-o', out_path,
            '-t', str(tmb)
        ]
        subprocess_runner(self.log_level, self.log_path).run(args)
        self.logger.info("Wrote TMB plot to {0}".format(out_path))
        return out_path

    def write_vaf_plot(self, out_dir):
        out_path = os.path.join(out_dir, 'vaf.jpeg')
        args = [
            os.path.join(self.r_script_dir, 'vaf_plot.R'),
            '-d', self.input_dir,
            '-o', out_path
        ]
        subprocess_runner(self.log_level, self.log_path).run(args)
        self.logger.info("Wrote VAF plot to {0}".format(out_path))
        return out_path

class fusion_reader(composer_base):

    # read files from an input directory and gather information on fusions
    DATA_FUSIONS_NEW = 'data_fusions_new_delimiter.txt'
    DATA_FUSIONS_OLD = 'data_fusions.txt'
    DATA_FUSIONS_ANNOTATED = 'data_fusions_oncokb_annotated.txt'
    FUSION_INDEX = 4
    HUGO_SYMBOL = 'Hugo_Symbol'

    def __init__(self, input_dir, log_level=logging.WARNING, log_path=None):
        self.logger = self.get_logger(log_level, __name__, log_path)
        self.input_dir = input_dir
        self.old_to_new_delimiter = self.read_fusion_delimiter_map()
        fusion_data = self.read_fusion_data()
        annotations = self.read_annotation_data()
        if set(fusion_data.keys()) != set(annotations.keys()):
            msg = "Distinct fusion identifiers and annotations do not match"
            self.logger.error(msg)
            raise RuntimeError(msg)
        [self.fusions, self.total_fusion_genes] = self._collate_row_data(fusion_data, annotations)

    def _collate_row_data(self, fusion_data, annotations):
        fusions = []
        fusion_genes = set()
        self.logger.debug("Starting to collate fusion table data.")
        intragenic = 0
        for fusion_id in fusion_data.keys():
            if len(fusion_data[fusion_id])==1:
                # add intragenic fusions to the gene count, then skip
                fusion_genes.add(fusion_data[fusion_id][0][self.HUGO_SYMBOL])
                intragenic += 1
                continue
            elif len(fusion_data[fusion_id]) >= 3:
                msg = "More than 2 fusions with the same name: {0}".format(fusion_id)
                self.logger.error(msg)
                raise RuntimeError(msg)
            gene1 = fusion_data[fusion_id][0][self.HUGO_SYMBOL]
            gene2 = fusion_data[fusion_id][1][self.HUGO_SYMBOL]
            fusion_genes.add(gene1)
            fusion_genes.add(gene2)
            frame = fusion_data[fusion_id][0]['Frame']
            ann = annotations[fusion_id]
            effect = ann['mutation_effect']
            oncokb_level = self.parse_oncokb_level(ann)
            fda = self.parse_max_oncokb_level_and_therapies(ann, self.FDA_APPROVED_LEVELS)
            [fda_level, fda_therapies] = fda
            inv = self.parse_max_oncokb_level_and_therapies(ann, self.INVESTIGATIONAL_LEVELS)
            [inv_level, inv_therapies] = inv
            fusions.append(
                fusion(
                    fusion_id,
                    self.old_to_new_delimiter[fusion_id],
                    gene1,
                    gene2,
                    frame,
                    effect,
                    oncokb_level,
                    fda_level,
                    fda_therapies,
                    inv_level,
                    inv_therapies
                )
            )
        total = len(fusions)
        total_fusion_genes = len(fusion_genes)
        msg = "Finished collating fusion table data. "+\
              "Found {0} fusion rows for {1} distinct genes; ".format(total, total_fusion_genes)+\
              "excluded {0} intragenic rows.".format(intragenic)
        self.logger.info(msg)
        return [fusions, total_fusion_genes]

    def get_fusions(self):
        return self.fusions

    def get_total_fusion_genes(self):
        return self.total_fusion_genes

    def read_annotation_data(self):
        # annotation file has exactly 1 line per fusion
        annotations_by_fusion = {}
        with open(os.path.join(self.input_dir, self.DATA_FUSIONS_ANNOTATED)) as data_file:
            for row in csv.DictReader(data_file, delimiter="\t"):
                annotations_by_fusion[row['Fusion']] = row
        return annotations_by_fusion

    def read_fusion_data(self):
        # data file has 1 or 2 lines per fusion (1 if it has an intragenic component, 2 otherwise)
        data_by_fusion = {}
        with open(os.path.join(self.input_dir, self.DATA_FUSIONS_OLD)) as data_file:
            for row in csv.DictReader(data_file, delimiter="\t"):
                fusion_id = row['Fusion']
                if fusion_id in data_by_fusion:
                    data_by_fusion[fusion_id].append(row)
                else:
                    data_by_fusion[fusion_id] = [row,]
        return data_by_fusion

    def read_fusion_delimiter_map(self):
        # read the mapping of fusion identifiers from old - to new :: delimiter
        # ugly workaround implemented in upstream R script; TODO refactor to something neater
        with open(os.path.join(self.input_dir, self.DATA_FUSIONS_OLD)) as file_old:
            old = [row[self.FUSION_INDEX] for row in csv.reader(file_old, delimiter="\t")]
        with open(os.path.join(self.input_dir, self.DATA_FUSIONS_NEW)) as file_new:
            new = [row[self.FUSION_INDEX] for row in csv.reader(file_new, delimiter="\t")]
        if len(old) != len(new):
            msg = "Fusion ID lists from {0} are of unequal length".format(report_dir)
            self.logger.error(msg)
            raise RuntimeError(msg)
        # first item of each list is the header, which can be ignored
        return {old[i]:new[i] for i in range(1, len(old))}

class fusion:
    # container for data relevant to reporting a fusion

    def __init__(
            self,
            fusion_id_old,
            fusion_id_new,
            gene1,
            gene2,
            frame,
            effect,
            oncokb_level,
            fda_level,
            fda_therapies,
            inv_level,
            inv_therapies
    ):
        self.fusion_id_old = fusion_id_old
        self.fusion_id_new = fusion_id_new
        self.gene1 = gene1
        self.gene2 = gene2
        self.frame = frame
        self.effect = effect
        self.oncokb_level = oncokb_level
        self.fda_level = fda_level
        self.fda_therapies = fda_therapies
        self.inv_level = inv_level
        self.inv_therapies = inv_therapies

    def get_fusion_id_old(self):
        return self.fusion_id_old

    def get_fusion_id_new(self):
        return self.fusion_id_new

    def get_genes(self):
        return [self.gene1, self.gene2]

    def get_frame(self):
        return self.frame

    def get_mutation_effect(self):
        return self.effect

    def get_oncokb_level(self):
        return self.oncokb_level

    def get_fda_level(self):
        return self.fda_level

    def get_fda_therapies(self):
        return self.fda_therapies

    def get_inv_level(self):
        return self.inv_level

    def get_inv_therapies(self):
        return self.inv_therapies

# TODO replace main() method with a script in the Djerba bin/ directory

def main():
    report_dir = sys.argv[1]
    out_dir = sys.argv[2]
    author = "Emmett Brown"
    assay_type = 'WGTS'
    clinical_report_json_composer(report_dir, author, assay_type, log_level=logging.INFO).run(out_dir)

if __name__ == '__main__':
    main()
