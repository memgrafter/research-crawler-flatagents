#!/bin/bash

sqlite3 -echo ../../arxiv_crawler/data/arxiv.sqlite ".schema paper_queue"
sqlite3 -echo ../../arxiv_crawler/data/arxiv.sqlite "select * from paper_queue where status='pending';"

echo "empty is None."
