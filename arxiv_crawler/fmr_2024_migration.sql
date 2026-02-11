-- FMR 2024 Column Migration
-- Adds a year-specific FMR score column to paper_relevance.

ALTER TABLE paper_relevance ADD COLUMN fmr_2024 REAL;
