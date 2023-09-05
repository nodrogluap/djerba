rm(list=ls())
library(CNTools)
library(optparse)
library(BSgenome.Hsapiens.UCSC.hg38)

# command line options
option_list = list(
  make_option(c("-b", "--basedir"), type="character", default=NULL, help="Location of R scripts", metavar="character"),
  make_option(c("-d", "--outdir"), type="character", default=NULL, help="output directory", metavar="character"),
  make_option(c("-s", "--segfile"), type="character", default=NULL, help="seg file", metavar="character"),
  make_option(c("-g", "--genebed"), type="character", default=NULL, help="bed file for gene identifying gene locations", metavar="character"),
  make_option(c("-o", "--oncolist"), type="character", default=NULL, help="oncoKB cancer genes", metavar="character"),
  make_option(c("-p", "--purity"), type="character", default=NULL, help="sample cellularity for CN cutoffs", metavar="character")
)

# get options
opt_parser <- OptionParser(option_list=option_list, add_help_option=FALSE)
opt <- parse_args(opt_parser)

# set better variable names
basedir <- opt$basedir
outdir <- opt$outdir
segfile <- opt$segfile
genebed <- opt$genebed
oncolist <- opt$oncolist
purity <- as.numeric(opt$purity)

# source functions
source(paste0(basedir, "/R/CNA_supporting_functions.r"))
data_dir <- paste(Sys.getenv(c("DJERBA_BASE_DIR")), 'data', sep='/')
centromeres_path <- paste(data_dir, 'hg38_centromeres.txt', sep='/')

###################### CNA #####################

if (is.null(segfile)) {
  print("No SEG file input, processing omitted")
} else {
  
  print("Finding CNA cutoffs for given purity")
  cutoffs <- log_r_cutoff_finder(purity)
  write.table(cutoffs, file=paste0(outdir, "/CNA_log_cutoffs.txt"), sep="\t", row.names=FALSE, quote=FALSE)
  
  print("Processing CNA data")
  CNAs <- preProcCNA(segfile, genebed, cutoffs, oncolist)
  
  print("writing seg file")
  # segs
  write.table(CNAs[[1]], file=paste0(outdir, "/data.seg"), sep="\t", row.names=FALSE, quote=FALSE)
  
  # log2cna
  print("writing log2 file")
  write.table(data.frame("Hugo_Symbol"=rownames(CNAs[[2]]), CNAs[[2]], check.names=FALSE),
              file=paste0(outdir, "/data_log2CNA.txt"), sep="\t", row.names=FALSE, quote=FALSE)
  
  # necessary file to find copy number profile of genes with small mutations
  write.table(data.frame("Hugo_Symbol"=rownames(CNAs[[3]]), CNAs[[3]], check.names=FALSE),
              file=paste0(outdir, "/data_CNA.txt"), sep="\t", row.names=FALSE, quote=FALSE)
  
  # write the short data_CNA file (non-zero, oncoKB genes) for oncoKB annotator
  print("writing non-diploid oncoKB genes")
  write.table(data.frame("Hugo_Symbol"=rownames(CNAs[[5]]), CNAs[[5]], check.names=FALSE),
              file=paste0(outdir, "/data_CNA_oncoKBgenes_nonDiploid.txt"), sep="\t", row.names=FALSE, quote=FALSE)
  
  if (is.null(centromeres_path)) {
    print("No centromeres file input, processing omitted")
  } else {
    segs <- read.delim(segfile, header=TRUE) # segmented data already
    print(names(segs))
    centromeres <- read.table(centromeres_path,header=T)
    
    arm_level_calls <- arm_level_caller(segs, centromeres, gain_threshold=cutoffs["LOG_R_GAIN"], shallow_deletion_threshold=cutoffs["LOG_R_HTZD"])
    write.table(arm_level_calls,file=paste0(outdir, "/arm_level_calls.txt"), sep="\t", row.names=FALSE, quote=FALSE, col.names = FALSE)
  }
  
}
