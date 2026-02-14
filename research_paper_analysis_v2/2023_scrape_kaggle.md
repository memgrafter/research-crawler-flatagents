# 2023 ML Batching

I would like to go further back, like 2017-2023.

The bottleneck is absolutely pdf download.

I found kaggle has free GCP buckets for 2023.


## Goal

Download all of arxiv 2023 with the buckets.

Maybe I can use gsutil to check file sizes.

This should be dead simple on stardart 18tb drive, then serve via network drive.


## Bulk access

from: https://www.kaggle.com/datasets/Cornell-University/arxiv


The full set of PDFs is available for free in the GCS bucket gs://arxiv-dataset or through Google API (json documentation and xml documentation).

You can use for example gsutil to download the data to your local machine.

# List files:
gsutil cp gs://arxiv-dataset/arxiv/

# Download pdfs from March 2020:
gsutil cp gs://arxiv-dataset/arxiv/arxiv/pdf/2003/ ./a_local_directory/

# Download all the source files
gsutil cp -r gs://arxiv-dataset/arxiv/  ./a_local_directory/

