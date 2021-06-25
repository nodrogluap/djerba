#! /usr/bin/env python3

import configparser
import hashlib
import json
import jsonschema
import os
import subprocess
import tempfile
import unittest
import djerba.util.constants as constants
from djerba.extract.sequenza import sequenza_extractor, SequenzaExtractionError
from djerba.extract.r_script_wrapper import r_script_wrapper
from djerba.render import html_renderer

class TestBase(unittest.TestCase):

    def getMD5(self, inputPath):
        md5 = hashlib.md5()
        with open(inputPath, 'rb') as f:
            md5.update(f.read())
        return md5.hexdigest()

    def run_command(self, cmd):
        """Run a command; in case of failure, capture STDERR."""
        result = subprocess.run(cmd, encoding=constants.TEXT_ENCODING, capture_output=True)
        try:
            result.check_returncode()
        except subprocess.CalledProcessError as err:
            msg = "Script failed with STDERR: "+result.stderr
            raise RuntimeError(msg) from err
        return result

    def setUp(self):
        self.testDir = os.path.dirname(os.path.realpath(__file__))
        self.dataDir = os.path.realpath(os.path.join(self.testDir, 'data'))
        # specify all non-public data paths relative to self.sup_dir
        # modified test provenance file gets its own environment variable
        sup_dir_var = 'DJERBA_TEST_DATA'
        provenance_var = 'DJERBA_TEST_PROVENANCE'
        self.sup_dir = os.environ.get(sup_dir_var)
        self.provenance_path = os.environ.get(provenance_var)
        if not (self.sup_dir):
            raise RuntimeError('Need to specify environment variable {0}'.format(sup_dir_var))
        elif not os.path.isdir(self.sup_dir):
            raise OSError("Supplementary directory path '{0}' is not a directory".format(self.sup_dir))
        if not self.provenance_path:
            raise RuntimeError('Need to specify environment variable {0}'.format(provenance_var))
        elif not os.path.isfile(self.provenance_path):
            raise OSError("Provenance path '{0}' is not a file".format(self.provenance_path))
        self.tmp = tempfile.TemporaryDirectory(prefix='djerba_')
        self.tmpDir = self.tmp.name
        self.schema_path = os.path.join(self.sup_dir, 'elba_config_schema.json')
        self.bed_path = os.path.join(self.sup_dir, 'S31285117_Regions.bed')
        self.maf_name = 'PANX_1249_Lv_M_WG_100-PM-013_LCM5.filter.deduped.realigned.recalibrated.mutect2.tumor_only.filtered.unmatched.maf.gz'
        self.expected_maf_path = os.path.join(self.sup_dir, self.maf_name)
        self.project = 'PASS01'
        self.donor = 'PANX_1249'
        with open(self.schema_path) as f:
            self.schema = json.loads(f.read())
        self.rScriptDir = os.path.realpath(os.path.join(self.testDir, '../lib/djerba/R/'))

    def tearDown(self):
        self.tmp.cleanup()

class TestRender(TestBase):

    def setUp(self):
        super().setUp()
        self.iniPath = os.path.join(self.dataDir, 'config_full.ini')

    def test_html(self):
        outDir = self.tmpDir
        outPath = os.path.join(outDir, 'djerba_test.html')
        reportDir = os.path.join(self.sup_dir, 'report_example')
        config = configparser.ConfigParser()
        config.read(self.iniPath)
        config['inputs']['out_dir'] = outDir
        test_renderer = html_renderer(config)
        test_renderer.run(reportDir, outPath)
        # TODO check file contents; need to omit the report date etc.
        self.assertTrue(os.path.exists(outPath))

class TestSequenzaExtractor(TestBase):

    def setUp(self):
        super().setUp()
        self.zip_path = os.path.join(self.sup_dir, 'PANX_1249_Lv_M_WG_100-PM-013_LCM5_results.zip')
        self.expected_gamma = 400
    
    def test_finder_script(self):
        """Test the command-line script to find gamma"""
        cmd = [
            "sequenza_gamma_selector.py",
            "--in", self.zip_path,
            "--verbose"
        ]
        result = self.run_command(cmd)
        with open(os.path.join(self.dataDir, 'gamma_test.tsv'), 'rt') as in_file:
            expected_params = in_file.read()
        self.assertEqual(int(result.stdout), self.expected_gamma)
        self.assertEqual(result.stderr, expected_params)

    def test_purity_ploidy(self):
        seqex = sequenza_extractor(self.zip_path)
        [purity, ploidy] = seqex.get_purity_ploidy()
        self.assertEqual(purity, 0.6)
        self.assertEqual(ploidy, 3.1)
        expected_segments = {
            50: 8669,
            100: 4356,
            200: 1955,
            300: 1170,
            400: 839,
            500: 622,
            600: 471,
            700: 407,
            800: 337,
            900: 284,
            1000: 245,
            1250: 165,
            1500: 123,
            2000: 84
        }
        self.assertEqual(seqex.get_segment_counts(), expected_segments)
        self.assertEqual(seqex.get_default_gamma(), self.expected_gamma)
        # test with alternate gamma
        [purity, ploidy] = seqex.get_purity_ploidy(gamma=50)
        self.assertEqual(purity, 0.56)
        self.assertEqual(ploidy, 3.2)
        # test with nonexistent gamma
        with self.assertRaises(SequenzaExtractionError):
            seqex.get_purity_ploidy(gamma=999999)

    def test_seg_file(self):
        seqex = sequenza_extractor(self.zip_path)
        seg_path = seqex.extract_seg_file(self.tmpDir)
        self.assertEqual(
            seg_path,
            os.path.join(self.tmpDir, 'gammas/400/PANX_1249_Lv_M_WG_100-PM-013_LCM5_Total_CN.seg')
        )
        self.assertEqual(self.getMD5(seg_path), '25b0e3c01fe77a28b24cff46081cfb1b')
        seg_path = seqex.extract_seg_file(self.tmpDir, gamma=1000)
        self.assertEqual(
            seg_path,
            os.path.join(self.tmpDir, 'gammas/1000/PANX_1249_Lv_M_WG_100-PM-013_LCM5_Total_CN.seg')
        )
        self.assertEqual(self.getMD5(seg_path), '5d433e47431029219b6922fba63a8fcf')
        with self.assertRaises(SequenzaExtractionError):
            seqex.extract_seg_file(self.tmpDir, gamma=999999)

class TestWrapper(TestBase):

    def test(self):
        iniPath = os.path.join(self.sup_dir, 'rscript_config_updated.ini')
        config = configparser.ConfigParser()
        config.read(iniPath)
        test_wrapper = r_script_wrapper(config)
        result = test_wrapper.run()
        self.assertEqual(0, result.returncode)

if __name__ == '__main__':
    unittest.main()
