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
import time
import subprocess
from datetime import datetime
from xero_python.accounting import AccountingApi, Invoice, LineItem, Contact, Contacts, Invoices, LineAmountTypes, CurrencyCode
from xero_python.api_client import ApiClient
from xero_python.api_client.configuration import Configuration
from xero_python.api_client.oauth2 import OAuth2Token
from xero_python.exceptions import AccountingBadRequestException

# Load environment variables
load_dotenv()
CLIENT_ID = os.getenv("CLIENT_ID_BILL")
CLIENT_SECRET = os.getenv("CLIENT_SECRET_BILL")
TENANT_ID = "4a3925fd-0785-4c67-a15e-e16eaca24081"  # Demo Company (UK) Tenant ID
REDIRECT_URI = "http://localhost:8080/callback"

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
                self.wfile.write(b"""
                <html>
                <head>
                    <title>Authorization Successful</title>
                    <style>
                        body {
                            font-family: Arial, sans-serif;
                            text-align: center;
                            padding: 50px;
                            background-color: #f5f5f5;
                        }
                        .success {
                            background-color: #4CAF50;
                            color: white;
                            padding: 20px;
                            border-radius: 5px;
                            max-width: 500px;
                            margin: 0 auto;
                        }
                    </style>
                </head>
                <body>
                    <div class="success">
                        <h1>Authorization Successful!</h1>
                        <p>You can now close this window and return to the application.</p>
                    </div>
                </body>
                </html>
                """)
            else:
                self.send_response(400)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(b"""
                <html>
                <head>
                    <title>Authorization Failed</title>
                    <style>
                        body {
                            font-family: Arial, sans-serif;
                            text-align: center;
                            padding: 50px;
                            background-color: #f5f5f5;
                        }
                        .error {
                            background-color: #f44336;
                            color: white;
                            padding: 20px;
                            border-radius: 5px;
                            max-width: 500px;
                            margin: 0 auto;
                        }
                    </style>
                </head>
                <body>
                    <div class="error">
                        <h1>Authorization Failed</h1>
                        <p>No authorization code received. Please try again.</p>
                    </div>
                </body>
                </html>
                """)
            
            # Stop the server
            threading.Thread(target=self.server.shutdown).start()

def get_access_token():
    try:
        # Start a local server to handle the OAuth callback
        server = HTTPServer(('localhost', 8080), OAuthCallbackHandler)
        server.auth_code = None
        
        # Start the server in a separate thread
        server_thread = threading.Thread(target=server.serve_forever)
        server_thread.daemon = True
        server_thread.start()
        
        # Construct the authorization URL with all necessary scopes
        auth_url = (
            "https://login.xero.com/identity/connect/authorize?"
            f"response_type=code&"
            f"client_id={CLIENT_ID}&"
            f"redirect_uri={REDIRECT_URI}&"
            f"scope=openid profile email accounting.transactions accounting.settings accounting.contacts accounting.attachments offline_access"
        )
        
        # Print the authorization URL
        print("\n" + "="*80)
        print("Please open this URL in your browser to authorize:")
        print(auth_url)
        print("="*80 + "\n")
        
        # Try different methods to open the browser
        browser_opened = False
        try:
            # Try using the default browser
            browser_opened = webbrowser.open(auth_url)
        except Exception as e:
            print(f"Error opening default browser: {str(e)}")
        
        if not browser_opened:
            try:
                # Try using Chrome specifically
                chrome_path = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
                if os.path.exists(chrome_path):
                    subprocess.Popen([chrome_path, auth_url])
                    browser_opened = True
            except Exception as e:
                print(f"Error opening Chrome: {str(e)}")
        
        if not browser_opened:
            try:
                # Try using Safari specifically
                safari_path = '/Applications/Safari.app/Contents/MacOS/Safari'
                if os.path.exists(safari_path):
                    subprocess.Popen([safari_path, auth_url])
                    browser_opened = True
            except Exception as e:
                print(f"Error opening Safari: {str(e)}")
        
        if not browser_opened:
            print("\nCould not open browser automatically.")
            print("Please manually copy and paste the URL above into your browser.")
        
        # Wait for the authorization code with a timeout
        timeout = 300  # 5 minutes
        start_time = time.time()
        while not server.auth_code and (time.time() - start_time) < timeout:
            time.sleep(1)
        
        # Stop the server
        server.shutdown()
        server.server_close()
        
        if not server.auth_code:
            print("\nAuthorization timed out. Please try again.")
            return None
        
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

def create_contact(contact_name, api_client):
    try:
        # Initialize the AccountingApi
        accounting_api = AccountingApi(api_client)
        
        # First, try to find an existing contact
        try:
            contacts = accounting_api.get_contacts(
                xero_tenant_id=TENANT_ID,
                where=f'Name=="{contact_name}"'
            )
            
            if contacts.contacts:
                contact_id = contacts.contacts[0].contact_id
                print(f"Found existing contact: {contact_name}")
                return contact_id
        except Exception as e:
            print(f"Error searching for contact {contact_name}: {str(e)}")
        
        # If contact not found, create a new one
        contact = Contact(
            name=contact_name,
            is_supplier=True,
            contact_status="ACTIVE"
        )
        
        # Create a Contacts object with the contact
        contacts = Contacts(contacts=[contact])
        
        # Create the contact using the SDK
        created_contacts = accounting_api.create_contacts(
            xero_tenant_id=TENANT_ID,
            contacts=contacts
        )
        
        if created_contacts.contacts:
            contact_id = created_contacts.contacts[0].contact_id
            print(f"Successfully created contact: {contact_name}")
            return contact_id
        else:
            print(f"Failed to create contact {contact_name}")
            return None
            
    except Exception as e:
        print(f"Error creating contact {contact_name}: {str(e)}")
        return None

def upload_bill_to_xero(data, api_client, pdf_path):
    try:
        # Initialize the AccountingApi
        accounting_api = AccountingApi(api_client)
        
        # First, create or get the contact
        contact_id = create_contact(data['customer_name'], api_client)
        if not contact_id:
            print(f"Failed to create contact for {data['customer_name']}")
            return False
            
        # Create line items
        line_items = []
        for item in data['line_items']:
            line_item = LineItem(
                description=item['description'],
                quantity=item['quantity'],
                unit_amount=item['unit_price'],
                account_code="400",
                tax_type="NONE",
                line_amount=item['total']
            )
            line_items.append(line_item)
        
        # Parse the date string into a datetime object
        date_obj = datetime.strptime(data['date'], '%Y-%m-%d')
        
        # Create the bill
        bill = Invoice(
            type="ACCPAY",
            contact=Contact(contact_id=contact_id),
            date=date_obj,
            due_date=date_obj,
            reference=data['number'],
            status="DRAFT",
            line_amount_types=LineAmountTypes.EXCLUSIVE,
            currency_code=CurrencyCode.GBP,
            currency_rate=1.0,
            line_items=line_items
        )
        
        # Create an Invoices object with the bill
        invoices = Invoices(invoices=[bill])
        
        # Upload the bill using the SDK
        print(f"Attempting to upload bill {data['number']} to Xero...")
        created_invoices = accounting_api.create_invoices(
            xero_tenant_id=TENANT_ID,
            invoices=invoices
        )
        
        if created_invoices.invoices:
            bill_id = created_invoices.invoices[0].invoice_id
            print(f"Successfully uploaded bill {data['number']} to Xero!")
            print(f"Xero Bill ID: {bill_id}")
            
            # Attach the PDF
            print(f"Attaching PDF to bill {bill_id}...")
            with open(pdf_path, 'rb') as pdf_file:
                accounting_api.create_invoice_attachment_by_file_name(
                    xero_tenant_id=TENANT_ID,
                    invoice_id=bill_id,
                    file_name=os.path.basename(pdf_path),
                    body=pdf_file.read()
                )
            print(f"Successfully attached PDF to bill {bill_id}")
            return True
        else:
            print(f"Failed to upload bill {data['number']}")
            return False
            
    except AccountingBadRequestException as e:
        print(f"Xero API error for bill {data['number']}:")
        print(f"Status: {e.status}")
        print(f"Response: {e.body}")
        return False
    except Exception as e:
        print(f"Error uploading bill {data['number']} to Xero: {str(e)}")
        return False

def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("Error: CLIENT_ID and CLIENT_SECRET must be set in .env file")
        return
        
    if not os.path.exists(INVOICE_DIR):
        print(f"Error: Invoice directory '{INVOICE_DIR}' does not exist")
        return
        
    # Get initial access token
    access_token = get_access_token()
    if not access_token:
        print("Error: Failed to get initial access token")
        return
        
    # Configure the Xero API client
    config = Configuration(
        debug=True,
        oauth2_token=OAuth2Token(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET
        )
    )
    
    api_client = ApiClient(config, pool_threads=1)
    
    # Configure token persistence
    @api_client.oauth2_token_getter
    def obtain_xero_oauth2_token():
        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "openid profile email accounting.transactions accounting.settings accounting.contacts accounting.attachments offline_access"
        }
    
    @api_client.oauth2_token_saver
    def store_xero_oauth2_token(token):
        # Store the token if needed
        pass
    
    # Set the token directly
    api_client.set_oauth2_token(obtain_xero_oauth2_token())
    
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
        success = upload_bill_to_xero(data, api_client, pdf_path)
        if success:
            print(f"Successfully processed {filename}")
        else:
            print(f"Failed to process {filename}")

if __name__ == '__main__':
    main()