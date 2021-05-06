"""Read input data for Djerba"""

import json

class reader:
    """Parent class for Djerba data readers."""

    GENE_METRICS_KEY = 'gene_metrics'
    GENE_NAME_KEY = 'Gene'
    ITEMS_KEY = 'items'
    PROPERTIES_KEY = 'properties'
    SAMPLE_INFO_KEY = 'sample_info'

    def __init__(self, config, schema_path):
        """Constructor for superclass; should be called in all subclass constructors"""
        # TODO does config need to be an argument here?
        with open(schema_path) as schema_file:
            schema = json.loads(schema_file.read())
        try:
            self.permitted_gene_attributes = set(
                schema[self.PROPERTIES_KEY][self.GENE_METRICS_KEY][self.ITEMS_KEY][self.PROPERTIES_KEY].keys()
            )
            self.permitted_sample_attributes = set(
                schema[self.PROPERTIES_KEY][self.SAMPLE_INFO_KEY][self.PROPERTIES_KEY].keys()
            )
        except KeyError as err:
            raise RuntimeError('Bad schema format; could not find expected keys') from err
        self.gene_metrics = {} # gene name -> dictionary of metrics
        self.sample_info = {}  # attribute name -> value

    def update_genes(self, genes):
        # genes is an array of dictionaries (as specified in the output schema) for the reader to update
        if len(genes)==0:
            genes = list(self.gene_metrics.values())
        else:
            # check consistency with previous entries, and update
            # cannot have two conflicting values for same gene and metric
            # TODO warn/error if genes and self.gene_metrics are of unequal length?
            for gene in genes:
                gene_name = gene[self.GENE_NAME_KEY]
                metrics_for_update = self.gene_metrics.get(gene_name)
                for key in metrics_for_update.keys():
                    if key in gene: # check if metric values are consistent
                        old = gene[key]
                        new = metrics_for_update[key]
                        if old != new:
                            msg = 'Inconsistent values for metric %s on gene %s. ' % (key, gene_name)+\
                                      'Expected: %s; Found: %s' % (str(old), str(new))
                            raise ValueError(msg)
                    else:
                        gene[key] = metrics_for_update[key]
        return genes

    def update_sample(self, info):
        # info is a dictionary of sample attributes for the reader to update
        # TODO should info be a class instead of a dictionary?
        for key in self.sample_info.keys():
            new_value = self.sample_info[key]
            if key in info:
                if info[key] != new_value:
                    msg = "Inconsistent values for sample_attribute %s: " % key +\
                        "Expected %s, found %s" % (str(info[key]), str(new_value))
                    raise ValueError(msg)
            else:
                info[key] = new_value
        return info

    def find_gene_attribute_errors(self):
        """Check all gene attribute names are consistent and defined in the schema"""
        expected_names = set()
        errors = []
        for gene_name in self.gene_metrics.keys():
            gene = self.gene_metrics[gene_name]
            names = set(gene.keys())
            if len(expected_names)==0:
                expected_names = names
            elif names != expected_names:
                errors.append("Gene %s has inconsistent attribute names" % gene_name)
            for name in names:
                if not name in self.permitted_gene_attributes:
                    errors.append("Attribute %s on gene %s is not defined in schema" % (name, gene_name))
        return errors

    def find_sample_attribute_errors(self):
        """Check all sample attribute names are defined in the schema"""
        errors = []
        for name in self.sample_info.keys():
            if not name in self.permitted_gene_attributes:
                errors.append("Attribute %s in sample_info is not defined in schema" % name)
        return errors

    def validate_with_schema(self):
        """Validate reader contents against the output schema -- call after reading any new data"""
        gene_errors = self.find_gene_attribute_errors()
        sample_errors = self.find_sample_attribute_errors()
        # TODO log the gene/sample error messages, if any
        if len(gene_errors)==0 and len(sample_errors)==0:
            return True
        else:
            return False
    

class reader_factory:
    """Given the config, construct a reader of the appropriate subclass"""

    GENE_METRICS_KEY = "gene_metrics"
    READER_CLASS_KEY = "reader_class"

    def __init__(self):
        pass

    def create_instance(self, config):
        """
        Return an instance of the reader class named in the config
        Config is a dictionary with a reader_class name, plus other parameters as needed
        """
        classname = config.get(self.READER_CLASS_KEY)
        if classname == None:
            msg = "Unknown or missing %s value in config. " % self.READER_CLASS_KEY
            #self.logger.error(msg)
            raise ValueError(msg)
        klass = globals().get(classname)
        #self.logger.debug("Created instance of %s" % classname)
        return klass(config)

class json_reader(reader):
    """
    Reader for JSON data.
    Supply input as JSON, as default/fallback if other sources not available
    """

    def __init__(self, config, schema_path):
        super().__init__(config, schema_path)
        genes = config.get(self.GENE_METRICS_KEY)
        for gene in genes:
            self.gene_metrics[gene.get(self.GENE_NAME_KEY)] = gene
        self.sample_info = config.get(self.SAMPLE_INFO_KEY)
        self.validate_with_schema()
