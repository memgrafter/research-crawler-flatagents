#!/bin/bash

sqlite3 -echo ../../arxiv_crawler/data/arxiv.sqlite ".schema worker_registry"
sqlite3 -echo ../../arxiv_crawler/data/arxiv.sqlite "select * from worker_registry where status='active';"

echo "empty is None."
