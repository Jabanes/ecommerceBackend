import os
import logging
from pymongo import MongoClient
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import ollama
import json
from datetime import datetime
from tqdm import tqdm
import re

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def clean_html_text(html_content):
    """Convert HTML to plain text, removing tags and cleaning up spacing."""
    if not html_content:
        return ""
    
    soup = BeautifulSoup(html_content, 'html.parser')
    # Remove script and style elements
    for script in soup(["script", "style"]):
        script.decompose()
    
    # Get text and clean up whitespace
    text = soup.get_text(separator=' ')
    # Normalize whitespace
    text = ' '.join(text.split())
    return text

def process_with_ollama(raw_product, clean_description):
    """Process raw product data with Ollama to get a structured and translated JSON object based on model.json."""
    
    # Prepare the input data for the prompt as a JSON string
    # This ensures all data types are serializable for the prompt.
    prompt_input_dict = {
      "title": raw_product.get("title", ""),
      "description_html_cleaned_text": clean_description,
      "original_data_images": raw_product.get("images", []),
      "original_data_variants": raw_product.get("variants", []),
      "original_data_full": {
          "_id": str(raw_product.get("_id")),
          "id": raw_product.get("id"),
          "title": raw_product.get("title"),
          "vendor": raw_product.get("vendor"),
          "productType": raw_product.get("productType"),
          "handle": raw_product.get("handle"),
          "status": raw_product.get("status"),
          "tags": raw_product.get("tags", []),
          "price": raw_product.get("price"),
          "currency": raw_product.get("currency"),
          "syncedAt": str(raw_product.get("syncedAt")) 
      }
    }
    prompt_input_json = json.dumps(prompt_input_dict, ensure_ascii=False, indent=2)

    prompt = f"""**Mission Objective:**
You are a product data expert and Hebrew marketing specialist. Your task is to transform raw product information, initially in English and often from a cleaned HTML description, into a predefined and consistent JSON data structure. You must meticulously extract and process the information, translating it into natural, fluent, catchy, and grammatically correct Hebrew. It is crucial to ensure no information is omitted, maintain strict consistency in the output structure across all products, and avoid robotic or overly literal translations.

**Input:**
You will receive a JSON object containing the following product details:

```json
{prompt_input_json}
```

**Output (Strict JSON Format):**
Your output MUST be a single, perfectly valid JSON object. Do not include any text before, after, or outside this JSON object. Adhere strictly to the field names (Keys) and data types (Value Types) as specified below. If a piece of information is unavailable in the input, the corresponding field should be null, an empty string "", or an empty array [], depending on its expected type.

```json
{{
  "productId": "string",
  "title": "string",
  "shortDescription": "string",
  "longDescription": "string",
  "mainImageUrl": "string (URL)",
  "galleryImages": ["string (URL)"],
  "brand": "string",
  "category": "string",
  "subCategory": "string",
  "keyFeatures": [
    "string"
  ],
  "technicalSpecifications": [
    {{ "key": "string", "value": "string" }}
  ],
  "dimensions": {{
    "length": "number | null",
    "width": "number | null",
    "height": "number | null",
    "unit": "string | null"
  }},
  "weight": {{
    "value": "number | null",
    "unit": "string | null"
  }},
  "material": ["string"],
  "colors": ["string"],
  "variants": [
    {{
      "id": "string",
      "title": "string",
      "sku": "string",
      "price": "number",
      "compareAtPrice": "number | null",
      "inventory": "number"
    }}
  ],
  "basePrice": "number",
  "currency": "string",
  "status": "string",
  "usageScenarios": ["string"]
}}
```

**Detailed Instructions for Extraction, Processing, and Translation (Mandatory Adherence):**

**productId (string):** Directly take the id (or _id) value from original_data_full.

**title (string):**
- **Extraction:** Take the full title value from the input.
- **Translation:** Translate the product title into Hebrew. It must be catchy, concise, accurate, and appealing.

**shortDescription (string):**
- **Extraction:** Derive from the description_html_cleaned_text.
- **Translation:** Create a concise and brief (up to 2 sentences) marketing-oriented Hebrew description that highlights the product's main benefit or use.

**longDescription (string):**
- **Extraction:** Use the majority of the text from description_html_cleaned_text.
- **Noise Removal (Mandatory):** Thoroughly remove ALL generic, irrelevant, or "supplier boilerplate" phrases.
- **Translation:** Translate the cleaned text into flowing, rich, and marketing-oriented Hebrew.

**mainImageUrl (string - URL) & galleryImages (array of strings - URLs):**
- **Extraction:** Directly take the URLs from the original_data_images array in the input. The first image's URL will be mainImageUrl, and the rest will populate galleryImages.

**brand (string):**
- **Extraction:** Extract the brand name from the text or from original_data_full.vendor. If the value is "NoEnName_Null", change it to "לא ידוע" (Unknown) or an empty string "".
- **Translation:** Translate to Hebrew if necessary (brand names often remain in English).

**category (string) & subCategory (string):**
- **Extraction:** Infer a general category and a more specific sub-category from the title and description_html_cleaned_text.
- **Translation:** Translate to Hebrew.

**keyFeatures (array of strings):**
- **Extraction:** Extract all key features and benefits mentioned in description_html_cleaned_text.
- **Translation:** Translate to short, precise, and appealing Hebrew.

**technicalSpecifications (array of objects {{"key": "string", "value": "string"}}):**
- **Extraction:** Extract all technical specifications appearing as key-value pairs.
- **Cleaning:** Remove irrelevant keys like "Brand Name", "Choice", "Origin", etc.
- **Translation:** Translate both the Key and the Value into Hebrew. Retain original units of measurement.

**dimensions (object):**
- **Extraction:** Carefully search `description_html_cleaned_text` for dimension patterns, such as "Dimensions: X" or "X x Y" or "X x Y x Z" followed by units.
- **Parsing Logic:**
    - **Prioritize Metric:** If both imperial (e.g., inches) and metric (e.g., cm) dimensions are provided (like `"3.3\\" x 2.4\\" (8.5 x 6 cm)"`), extract the metric values (8.5 and 6) and their unit (cm).
    - **Standard Format:** For a pattern like "L x W x H unit" or "L x W (L_metric x W_metric unit)", map the first value to `length`, the second to `width`, and the third (if present) to `height`.
    - **Numerical Conversion:** Ensure `length`, `width`, and `height` are stored as numbers (floats or integers).
- **Unit Extraction:** Extract the unit of measurement (e.g., "cm", "inches", "mm", "meters", "ft").
- **Default if Not Found:** If no dimension information is found, all sub-fields (`length`, `width`, `height`, `unit`) should be `null`.
- **Translation:** Translate the unit to Hebrew (e.g., "cm" to "ס\\"מ", "inches" to "אינץ'", "mm" to "מ\\"מ"). The numerical values remain as numbers.

**weight (object):**
- **Extraction:** Extract numerical values and units for weight or capacity (e.g., "Weight: 5kg", "Capacity 500lbs").
- **Numerical Conversion:** The `value` in the JSON output MUST be a number (float or integer).
- **Unit Extraction:** Extract the unit ("kg", "lbs").
- **Default:** If no weight is found, `value` MUST be `null` and `unit` MUST be `null`.
- **Translation:** Translate the extracted unit to its Hebrew equivalent (e.g., "kg" to "ק\\"ג", "lbs" to "ליברות").

**material (array of strings):**
- **Extraction:** Extract the main materials.
- **Translation:** Translate to Hebrew.

**colors (array of strings):**
- **Extraction:** Extract available product colors.
- **Translation:** Translate color names to Hebrew.

**variants (array of objects):**
- **Extraction:** Take the original_data_variants array directly.
- **Translation (if applicable):** Translate descriptive terms in the variant titles to Hebrew.

**basePrice (number) & currency (string) & status (string):**
- **Extraction:** Take directly from original_data_full.

**usageScenarios (array of strings):**
- **Extraction:** Extract the common uses or applications.
- **Translation:** Translate to natural Hebrew.

**Crucial General Rules:**
- **Fluent & Natural Hebrew:** The translation must sound native.
- **Error-Free:** Perfect Hebrew grammar and spelling.
- **Information Completeness:** Do not omit any relevant information.
- **Consistency:** Guarantee consistent output structure.
"""

    try:
        response = ollama.generate(model="command-r", prompt=prompt)
        result = response['response'].strip()
        
        # Clean up the response to extract just the JSON
        try:
            # Use regex to find the JSON object. This is more robust against extra text.
            json_match = re.search(r'\{[\s\S]*\}', result)
            
            if json_match is None:
                logging.error("Could not find valid JSON in response")
                logging.error(f"Raw response: {result}")
                return None
                
            json_str = json_match.group(0)
            parsed_json = json.loads(json_str)
            return parsed_json
            
        except json.JSONDecodeError as e:
            logging.error(f"Invalid JSON from Ollama: {e}")
            logging.error(f"Raw response: {result}")
            logging.error(f"Attempted to parse: {json_str}")
            return None
            
    except Exception as e:
        logging.error(f"Error calling Ollama: {e}")
        return None

def refine_raw_products():
    """
    Reads raw product data from MongoDB, processes with Ollama for structured, translated
    data based on model.json, and saves to the refined collection.
    """
    print("Starting refine_products.py script...")
    
    # Initialize Ollama client
    try:
        ollama.list()
        logging.info("Successfully connected to Ollama.")
    except Exception as e:
        logging.error(f"Could not connect to Ollama. Please ensure Ollama is running. Error: {e}")
        return

    # Load MongoDB settings
    mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    mongo_db_name = os.getenv("MONGO_DB_NAME", "dropship_db")
    
    try:
        client = MongoClient(mongo_uri)
        db = client[mongo_db_name]
        db.command('ping')
        logging.info("Successfully connected to MongoDB")

        raw_collection = db['raw_products']
        refined_collection = db['refined_products']
        
        raw_products = list(raw_collection.find())
        logging.info(f"Found {len(raw_products)} raw products to process.")

        if len(raw_products) == 0:
            logging.warning("No products found in raw_products collection!")
            return

        for raw_product in tqdm(raw_products, desc="Processing Products"):
            product_id = raw_product.get('_id', 'No ID')
            title = raw_product.get('title', '')
            logging.info(f"Processing product: {title}")
            
            # Clean HTML description
            clean_description = clean_html_text(raw_product.get('description', ''))
            
            # Process with Ollama to get structured, translated data
            refined_content = process_with_ollama(raw_product, clean_description)
            
            if refined_content is None:
                logging.error(f"Failed to process product {product_id}, skipping...")
                continue
                
            # Create refined product document
            refined_product = {
                '_id': raw_product['_id'],
                'original_data': raw_product,
                'refined_content': refined_content,
                'refinedAt': datetime.now().isoformat()
            }
            
            # Save to refined collection
            result = refined_collection.replace_one(
                {'_id': refined_product['_id']}, 
                refined_product, 
                upsert=True
            )

            if result.upserted_id:
                logging.info(f"Created new refined product: {title}")
            elif result.modified_count > 0:
                logging.info(f"Updated existing refined product: {title}")

        logging.info("Product refinement complete.")

    except Exception as e:
        logging.error(f"Error during processing: {e}")
    finally:
        if 'client' in locals():
            client.close()
            logging.info("MongoDB connection closed.")

if __name__ == "__main__":
    refine_raw_products() 