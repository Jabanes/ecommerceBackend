# Master Prompt for Product Refinement

**Task Objective:**
My primary goal is to act as a robust data processing and translation engine. I will be provided with raw product data in a file named `rawdata.json`. My task is to read this file, then extract, clean, process, and meticulously translate each product into a predefined, standardized JSON structure. All textual content must be in natural, grammatically correct, and marketable Hebrew. The output will be saved to a new file, `refined_data.json`, which must strictly adhere to the specified schema, be comprehensive, consistent across all products, and omit no relevant information.

**Input Data:**
A JSON file named `rawdata.json` containing an array of raw product objects.

**Output Data:**
A single JSON file named `refined_data.json` containing an array of processed product objects, strictly conforming to the schema below.

**Output JSON Schema:**
```json
{
  "productId": "string",
  "title": "string",
  "shortDescription": "string",
  "longDescription": "string",
  "mainImageUrl": "string (URL)",
  "galleryImages": ["string (URL)"],
  "brand": "string",
  "category": "string",
  "subCategory": "string",
  "keyFeatures": ["string"],
  "technicalSpecifications": [{ "key": "string", "value": "string" }],
  "dimensions": {
    "length": "number | null",
    "width": "number | null",
    "height": "number | null",
    "unit": "string | null"
  },
  "weight": {
    "value": "number | null",
    "unit": "string | null"
  },
  "material": ["string"],
  "colors": ["string"],
  "variants": [
    {
      "id": "string",
      "title": "string",
      "sku": "string",
      "price": "number",
      "compareAtPrice": "number | null",
      "inventory": "number"
    }
  ],
  "basePrice": "number",
  "currency": "string",
  "status": "string",
  "usageScenarios": ["string"]
}
```

**Transformation Rules (Field-by-Field Instructions):**
I will apply the following logic to every product object in the input file:

1.  **`productId`**: Extract `_id` directly.
2.  **`title`**: Translate `raw_data.title` into catchy, marketable Hebrew.
3.  **`shortDescription`**: Create a 1-2 sentence summary from the HTML-stripped `raw_data.description` and translate it into appealing Hebrew.
4.  **`longDescription`**:
    *   Strip all HTML tags from `raw_data.description`.
    *   Remove all boilerplate text ("Brand Name: NoEnName_Null", "Choice: yes", "Origin: Mainland China", etc.) and internal headings ("SPECIFICATIONS", "Feature:").
    *   Translate the cleaned text into rich, persuasive Hebrew.
5.  **Images (`mainImageUrl`, `galleryImages`)**: Use the `url` values from `raw_data.images`. The first is the main image, the rest go into the gallery array. URLs are not translated.
6.  **`brand`**: Extract from `raw_data.vendor` or description. If "NoEnName_Null", set to "לא ידוע". Translate only if the brand is a generic term.
7.  **Category (`category`, `subCategory`)**: Infer from the title and description, then translate into appropriate Hebrew terms.
8.  **`keyFeatures`**: Extract key selling points from the cleaned description, make them concise, and translate into punchy Hebrew bullet points.
9.  **`technicalSpecifications`**: Extract key-value pairs from the cleaned description. Remove irrelevant keys. Translate both the key and the value to Hebrew, retaining original units.
10. **`dimensions`**:
    *   Search the cleaned description for dimension patterns.
    *   Prioritize metric units if both imperial and metric are present.
    *   Parse values into `length`, `width`, `height` as numbers (float/integer).
    *   If no dimensions are found, all sub-fields will be `null`.
    *   Translate the extracted unit to Hebrew (cm -> ס"מ, inches -> אינץ').
11. **`weight`**:
    *   Extract numerical weight/capacity and its unit.
    *   The `value` must be a number (float/integer).
    *   If not found, `value` and `unit` will be `null`.
    *   Translate the unit to Hebrew (lbs -> ליברות, kg -> ק"ג).
12. **`material`**: Extract materials and translate them to Hebrew.
13. **`colors`**: Extract colors from the description or variant titles and translate them to Hebrew.
14. **`variants`**: Map the `raw_data.variants` array directly, but translate the `title` of each variant into Hebrew.
15. **Price & Status (`basePrice`, `currency`, `status`)**: Map `raw_data.price` and `raw_data.currency` directly. Translate `raw_data.status` to Hebrew (e.g., "ACTIVE" to "פעיל").
16. **`usageScenarios`**: Infer common uses from the description/title and translate them into natural Hebrew phrases. 