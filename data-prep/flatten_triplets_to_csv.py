import json
import csv
import os
from pathlib import Path

# Configuration
base_path = "./financial_kg_sp100_2024_qwen/Qwen2.5-72B-Instruct/multi_pass"
output_chunks_dir = "output/chunks"
output_triplets_dir = "output/triplets"

# Create output directories if they don't exist
os.makedirs(output_chunks_dir, exist_ok=True)
os.makedirs(output_triplets_dir, exist_ok=True)

# Find all JSON files in the multi_pass directory
json_files = []
for company_folder in Path(base_path).iterdir():
    if company_folder.is_dir() and not company_folder.name.startswith('.'):
        # Look for JSON files in the 2024 subdirectory
        year_folder = company_folder / "2024"
        if year_folder.exists():
            for json_file in year_folder.glob("*_triplets_*.json"):
                json_files.append({
                    'path': json_file,
                    'ticker': company_folder.name
                })

total_files = len(json_files)
print(f"Found {total_files} JSON files to process\n")

# Process each JSON file
total_chunks = 0
total_triplets = 0

for idx, file_info in enumerate(json_files, 1):
    json_path = file_info['path']
    ticker = file_info['ticker']
    
    print(f"[{idx}/{total_files}] Processing {ticker}...", end=" ")
    
    try:
        # Read JSON file
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        rows_triplets = []
        rows_chunks = []
        
        # Process each record
        for record in data:
            document_id = record.get("source_file")
            chunk_id = record.get("chunk_id")
            chunk_text = record.get("chunk_text", "").replace("\n", " ").replace("\r", " ")
            page_id = record.get("page_id")
            triplets = record.get("chunk_triplet", {})
            
            chunk_key = f"{document_id}_{page_id}_{chunk_id}"
            
            # For chunk-level CSV
            rows_chunks.append({
                "document_id": document_id,
                "page_id": page_id,
                "chunk_id": chunk_id,
                "chunk_key": chunk_key,
                "chunk_text": chunk_text
            })
            
            # For triplet-level CSV
            for _, values in triplets.items():
                if len(values) != 5:
                    continue
                subj, subj_type, relation, obj, obj_type = values
                
                rows_triplets.append({
                    "document_id": document_id,
                    "page_id": page_id,
                    "chunk_id": chunk_id,
                    "chunk_key": chunk_key,
                    "entity_id": subj,
                    "entity_cat": subj_type,
                    "event_id": obj,
                    "event_cat": obj_type,
                    "relation": relation
                })
        
        # Write chunk-level CSV
        output_chunks_file = os.path.join(output_chunks_dir, f"{ticker}_chunks.csv")
        with open(output_chunks_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "document_id",
                    "page_id",
                    "chunk_id",
                    "chunk_key",
                    "chunk_text"
                ]
            )
            writer.writeheader()
            writer.writerows(rows_chunks)
        
        # Write triplet-level CSV
        output_triplets_file = os.path.join(output_triplets_dir, f"{ticker}_triplets.csv")
        with open(output_triplets_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "document_id",
                    "page_id",
                    "chunk_id",
                    "chunk_key",
                    "entity_id",
                    "entity_cat",
                    "event_id",
                    "event_cat",
                    "relation"
                ]
            )
            writer.writeheader()
            writer.writerows(rows_triplets)
        
        total_chunks += len(rows_chunks)
        total_triplets += len(rows_triplets)
        
        print(f"✓ ({len(rows_chunks)} chunks, {len(rows_triplets)} triplets)")
        
    except Exception as e:
        print(f"✗ Error: {str(e)}")
        continue

print(f"\n{'='*60}")
print(f"Processing Complete!")
print(f"{'='*60}")
print(f"Total companies processed: {total_files}")
print(f"Total chunks written: {total_chunks:,}")
print(f"Total triplets written: {total_triplets:,}")
print(f"\nOutput locations:")
print(f"  Chunks:   {output_chunks_dir}/")
print(f"  Triplets: {output_triplets_dir}/")
