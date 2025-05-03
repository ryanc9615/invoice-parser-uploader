import os
import pdfplumber
import re
import requests
from dotenv import load_dotenv
import base64
import json
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import urllib.parse
import xml.etree.ElementTree as ET

# Load environment variables
load_dotenv()
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
TENANT_ID = os.getenv("TENANT_ID")
REDIRECT_URI = "http://localhost:5050/callback"

INVOICE_DIR = 'invoices'

class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/callback'):
            # Parse the query parameters
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            
            # Get the authorization code
            code = params.get('code', [None])[0]
            
            if code:
                # Store the code
                self.server.auth_code = code
                
                # Send response to browser
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(b"Authorization successful! You can close this window.")
            else:
                self.send_response(400)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(b"Authorization failed! No code received.")
            
            # Stop the server
            threading.Thread(target=self.server.shutdown).start()

def get_access_token():
    try:
        # Start a local server to handle the OAuth callback
        server = HTTPServer(('localhost', 5050), OAuthCallbackHandler)
        server.auth_code = None
        
        # Start the server in a separate thread
        server_thread = threading.Thread(target=server.serve_forever)
        server_thread.daemon = True
        server_thread.start()
        
        # Construct the authorization URL
        auth_url = (
            "https://login.xero.com/identity/connect/authorize?"
            f"response_type=code&"
            f"client_id={CLIENT_ID}&"
            f"redirect_uri={REDIRECT_URI}&"
            f"scope=openid profile email accounting.transactions accounting.settings offline_access"
        )
        
        # Open the authorization URL in the default browser
        print("Opening browser for authorization...")
        webbrowser.open(auth_url)
        
        # Wait for the authorization code
        while not server.auth_code:
            pass
        
        # Stop the server
        server.shutdown()
        server.server_close()
        
        # Exchange the authorization code for an access token
        token_url = "https://identity.xero.com/connect/token"
        
        # Create the basic auth header
        auth_string = f"{CLIENT_ID}:{CLIENT_SECRET}"
        auth_bytes = auth_string.encode('ascii')
        base64_auth = base64.b64encode(auth_bytes).decode('ascii')
        
        # Set up headers for token request
        headers = {
            "Authorization": f"Basic {base64_auth}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        # Set up data for token request
        data = {
            "grant_type": "authorization_code",
            "code": server.auth_code,
            "redirect_uri": REDIRECT_URI
        }
        
        # Request access token
        response = requests.post(token_url, headers=headers, data=data)
        
        if response.status_code == 200:
            token_data = response.json()
            return token_data.get('access_token')
        else:
            print(f"Error getting access token: {response.text}")
            return None
            
    except Exception as e:
        print(f"Error getting access token: {str(e)}")
        return None

def extract_invoice_data(pdf_path):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = ''
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + '\n'
        
        # Print extracted text for debugging
        print(f"\nExtracted text from {pdf_path}:")
        print(text[:500] + "...")  # Print first 500 chars for debugging
        
        # Extract customer name (look for text after "Bill To:" or similar)
        customer_match = re.search(r"Bill\s*To:[\s\n]+([^\n]+)", text, re.IGNORECASE)
        customer_name = customer_match.group(1).strip() if customer_match else "Demo Customer"
        
        # Extract line items
        line_items = []
        # Look for lines that contain a description, quantity, unit price, and total
        item_pattern = r"([^\n]+?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:,\d+)?\.\d{2})\s+(\d+(?:,\d+)?\.\d{2})"
        for match in re.finditer(item_pattern, text):
            description = match.group(1).strip()
            quantity = float(match.group(2))
            unit_price = float(match.group(3).replace(',', ''))
            total = float(match.group(4).replace(',', ''))
            line_items.append({
                'description': description,
                'quantity': quantity,
                'unit_price': unit_price,
                'total': total
            })
        
        date_match = re.search(r"Date:\s*(\d{4}-\d{2}-\d{2})", text)
        number_match = re.search(r"Invoice Number:\s*(INV-\d{4})", text)
        total_match = re.search(
            r"Grand\s*Total[:\s]*\$?([\d,]+\.\d{2})",
            text, re.IGNORECASE
        )
        
        data = {
            'date': date_match.group(1) if date_match else None,
            'number': number_match.group(1) if number_match else None,
            'total': total_match.group(1) if total_match else None,
            'customer_name': customer_name,
            'line_items': line_items
        }
        
        print(f"Extracted data: {data}")
        return data
    except Exception as e:
        print(f"Error extracting data from {pdf_path}: {str(e)}")
        return None

def upload_invoice_to_xero(data, access_token, pdf_path):
    try:
        # Set up headers
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Xero-tenant-id": TENANT_ID,
            "Content-Type": "application/json"
        }
        
        # Create line items from extracted data
        line_items = []
        for item in data['line_items']:
            line_items.append({
                "Description": item['description'],
                "Quantity": item['quantity'],
                "UnitAmount": item['unit_price'],
                "AccountCode": "200"
            })
        
        # Create invoice payload
        invoice_payload = {
            "Invoices": [{
                "Type": "ACCREC",
                "Contact": {
                    "Name": data['customer_name']
                },
                "LineItems": line_items,
                "Date": data['date'],
                "InvoiceNumber": data['number'],
                "Status": "DRAFT"
            }]
        }
        
        # Print debug information
        print(f"\nDebug - Request Headers: {headers}")
        print(f"Debug - Request Payload: {json.dumps(invoice_payload, indent=2)}")
        
        # Upload to Xero
        print(f"Attempting to upload invoice {data['number']} to Xero...")
        response = requests.post(
            "https://api.xero.com/api.xro/2.0/Invoices",
            headers=headers,
            json=invoice_payload
        )
        
        # Print response details for debugging
        print(f"Debug - Response Status Code: {response.status_code}")
        print(f"Debug - Response Headers: {dict(response.headers)}")
        print(f"Debug - Response Text: {response.text}")
        
        if response.status_code == 200:
            try:
                # Parse XML response
                root = ET.fromstring(response.text)
                # Check if the response contains an Invoice element
                invoice = root.find('.//Invoice')
                if invoice is not None:
                    invoice_id = invoice.find('InvoiceID').text
                    print(f"Successfully uploaded invoice {data['number']} to Xero!")
                    print(f"Xero Invoice ID: {invoice_id}")
                    
                    # Attach the PDF to the invoice
                    print(f"Attaching PDF to invoice {invoice_id}...")
                    attach_pdf_to_invoice(invoice_id, pdf_path, access_token)
                    
                    return True
                else:
                    print(f"Failed to upload invoice {data['number']}. No invoice returned.")
                    return False
            except ET.ParseError as e:
                print(f"Error parsing XML response: {str(e)}")
                return False
        elif response.status_code == 401:
            print("Access token expired. Getting new token...")
            new_token = get_access_token()
            if new_token:
                return upload_invoice_to_xero(data, new_token, pdf_path)
            else:
                print("Failed to get new access token")
                return False
        else:
            print(f"Xero API error for invoice {data['number']}:")
            print(f"Status: {response.status_code}")
            print(f"Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"Error uploading invoice {data['number']} to Xero: {str(e)}")
        return False

def attach_pdf_to_invoice(invoice_id, pdf_path, access_token):
    try:
        # Set up headers for file upload
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Xero-tenant-id": TENANT_ID,
            "Accept": "application/json"
        }
        
        # Prepare the file for upload
        with open(pdf_path, 'rb') as pdf_file:
            files = {
                'file': (os.path.basename(pdf_path), pdf_file, 'application/pdf')
            }
            
            # Upload the file
            response = requests.post(
                f"https://api.xero.com/api.xro/2.0/Invoices/{invoice_id}/Attachments",
                headers=headers,
                files=files
            )
            
            if response.status_code == 200:
                print(f"Successfully attached PDF to invoice {invoice_id}")
                return True
            else:
                print(f"Failed to attach PDF to invoice {invoice_id}:")
                print(f"Status: {response.status_code}")
                print(f"Response: {response.text}")
                return False
                
    except Exception as e:
        print(f"Error attaching PDF to invoice {invoice_id}: {str(e)}")
        return False

def main():
    if not CLIENT_ID or not CLIENT_SECRET or not TENANT_ID:
        print("Error: CLIENT_ID, CLIENT_SECRET, and TENANT_ID must be set in .env file")
        return
        
    if not os.path.exists(INVOICE_DIR):
        print(f"Error: Invoice directory '{INVOICE_DIR}' does not exist")
        return
        
    # Get initial access token
    access_token = get_access_token()
    if not access_token:
        print("Error: Failed to get initial access token")
        return
        
    pdf_files = [f for f in os.listdir(INVOICE_DIR) if f.lower().endswith('.pdf')]
    if not pdf_files:
        print(f"No PDF files found in '{INVOICE_DIR}'")
        return
        
    print(f"Found {len(pdf_files)} PDF files to process")
    
    for filename in pdf_files:
        print(f"\nProcessing {filename}...")
        pdf_path = os.path.join(INVOICE_DIR, filename)
        
        # Extract data from PDF
        data = extract_invoice_data(pdf_path)
        if not data:
            print(f"Skipping {filename} - could not extract data")
            continue
            
        # Check if all required fields are present
        if not all(data.values()):
            print(f"Skipping {filename} - missing required fields: {data}")
            continue
            
        # Upload to Xero
        success = upload_invoice_to_xero(data, access_token, pdf_path)
        if success:
            print(f"Successfully processed {filename}")
        else:
            print(f"Failed to process {filename}")

if __name__ == '__main__':
    main()