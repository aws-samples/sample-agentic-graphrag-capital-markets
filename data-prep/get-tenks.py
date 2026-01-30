import requests
import json
import pandas as pd
import boto3
import argparse
import time
from bs4 import BeautifulSoup

# Parse command line arguments
parser = argparse.ArgumentParser(description='Download 10-K filings from SEC EDGAR to S3')
parser.add_argument('--bucket', required=True, help='S3 bucket name')
parser.add_argument('--email', required=True, help='Contact email for SEC User-Agent (required by SEC)')
args = parser.parse_args()

s3_client = boto3.client('s3')
bucket = args.bucket

# Track success and failures
success_count = 0
failed_tickers = []
tickers = [
    'AAPL', 'ABBV', 'ABT', 'ACN', 'ADBE', 'AIG', 'AMD', 'AMGN', 'AMT', 'AMZN',
    'AVGO', 'AXP', 'BA', 'BAC', 'BK', 'BKNG', 'BKRB', 'BLK', 'BMY', 'C',
    'CAT', 'CHTR', 'CL', 'CMCSA', 'COF', 'COP', 'COST', 'CRM', 'CSCO', 'CVS',
    'CVX', 'DE', 'DHR', 'DIS', 'DUK', 'EMR', 'FDX', 'GD', 'GE', 'GILD',
    'GM', 'GOOG', 'GOOGL', 'GS', 'HD', 'HON', 'IBM', 'INTC', 'INTU', 'ISRG',
    'JNJ', 'JPM', 'KO', 'LIN', 'LLY', 'LMT', 'LOW', 'MA', 'MCD', 'MDLZ',
    'MDT', 'MET', 'META', 'MMM', 'MO', 'MRK', 'MS', 'MSFT', 'NEE', 'NFLX',
    'NKE', 'NOW', 'NVDA', 'ORCL', 'PEP', 'PFE', 'PG', 'PLTR', 'PM', 'PYPL',
    'QCOM', 'RTX', 'SBUX', 'SCHW', 'SO', 'SPG', 'T', 'TGT', 'TMO', 'TMUS',
    'TSLA', 'TXN', 'UNH', 'UNP', 'UPS', 'USB', 'V', 'VZ', 'WFC', 'WMT',
    'XOM'
]
print(f"Total tickers: {len(tickers)}")
headers = {
    "User-Agent": args.email,  # Required by SEC
}
ERRORS = []
# Step 1: Get CIK from ticker
def get_cik(ticker):
    url = "https://www.sec.gov/files/company_tickers.json"
    response = requests.get(url, headers=headers, timeout=30)
    data = response.json()
    for item in data.values():
        if item['ticker'].lower() == ticker.lower():
            return str(item['cik_str']).zfill(10)
    return None
# Step 2: Get 10-K filing metadata
def get_10k_metadata(cik):
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    response = requests.get(url, headers=headers, timeout=30)
    data = response.json()
    for filing in data['filings']['recent']['form']:
        if filing == '10-K':
            index = data['filings']['recent']['form'].index(filing)
            accession = data['filings']['recent']['accessionNumber'][index].replace("-", "")
            return accession
    return None
# Step 3: Download 10-K report
def download_10k(cik, ticker, accession, bucket, prefix=None):
    global success_count
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/index.json"
    response = requests.get(url, headers=headers, timeout=30)
    
    # Handle JSON decode errors (rate limiting or API issues)
    try:
        files = response.json()['directory']['item']
    except (requests.exceptions.JSONDecodeError, KeyError) as e:
        print(f"{ticker} SEC API returned invalid response, skipping...")
        failed_tickers.append((ticker, "API error"))
        return
    # Find primary document named as '{ticker}-{YYYYMMDD}.htm'
    for file in files:
        if file['name'].endswith('.htm') or file['name'].endswith('.html'):
            index_header_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{file['name']}"
            index_header_resp = requests.get(index_header_url, headers=headers, timeout=30)
            if index_header_resp.status_code == 200:
                soup = BeautifulSoup(index_header_resp.content, 'html.parser')
                # Get the pre tag content
                try:
                    pre_content = soup.find('pre').text
                except AttributeError:
                    print(f"{ticker} 10-K has unexpected HTML format (no <pre> tag), skipping...")
                    return
                # Split the content by <DOCUMENT>
                documents = pre_content.split('<DOCUMENT>')
                # Iterate through documents
                for document in documents:
                    if '<TYPE>10-K' in document:
                        # Find the FILENAME line
                        for line in document.split('\n'):
                            if '<FILENAME>' in line:
                                # Extract filename between <FILENAME> tags
                                filename = line.split('<FILENAME>')[1].split('<')[0]
                                file_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{filename.strip()}"
                                file_resp = requests.get(file_url, headers=headers, timeout=30)
                                s3_client.put_object(Body=file_resp.text, Bucket=bucket, Key=f'{prefix}{filename.strip()}')
                                print(f"{ticker} 10-K downloaded successfully as {prefix}{filename.strip()}")
                                success_count += 1
                                return
    print(f"{ticker} 10-K file not found")
    failed_tickers.append((ticker, "File not found"))
# Run the pipeline
for ticker in tickers:
    cik = get_cik(ticker)
    if cik:
        accession = get_10k_metadata(cik)
        if accession:
            download_10k(cik, ticker, accession, bucket, prefix='tenks/')
            # Rate limiting: SEC requests 10 requests per second max
            time.sleep(0.1)  # 100ms delay between tickers
        else:
            print(f"{ticker} 10-K accession not found.")
            failed_tickers.append((ticker, "No 10-K filing found"))
    else:
        print(f"{ticker} CIK not found.")
        failed_tickers.append((ticker, "CIK not found"))

# Print summary
print("\n" + "="*60)
print("DOWNLOAD SUMMARY")
print("="*60)
print(f"Total tickers processed: {len(tickers)}")
print(f"Successfully downloaded: {success_count}")
print(f"Failed: {len(failed_tickers)}")

if failed_tickers:
    print("\nFailed tickers:")
    for ticker, reason in failed_tickers:
        print(f"  {ticker}: {reason}")
    print(f"\nNote: Some failures may be due to SEC rate limiting.")
    print(f"Wait a few minutes and re-run the script to retry failed tickers.")
else:
    print("\n✓ All tickers downloaded successfully!")

print(f"\nFiles uploaded to: s3://{bucket}/tenks/")
