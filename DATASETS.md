# Fashion datasets: research, licences and how to import

Checked on 24 Oct 2026 against the official pages. **Licences change - re-read them before you rely on this file.**
Dataset items are stored as `ownership = 'inspiration'`, in `data/external/<source>/`, completely separate from your private
wardrobe photos (`data/wardrobe/`). They are used only as reference pictures/gallery for *New Outfit Inspiration* and are never
presented as clothes you own. The folder is git-ignored and excluded from the project ZIP: **do not redistribute dataset images.**

## Summary

| Dataset | Annotations licence | Images | Commercial use | Download | Redistribution |
|---|---|---|---|---|---|
| **Fashionpedia** (https://fashionpedia.github.io/home/index.html) | CC BY 4.0 (attribution required) | Fashionpedia does **not** own them. Use must follow the terms of the original sources (Flickr, Unsplash, Burst by Shopify, Freestocks, Kaboompics, Pexels). You accept full responsibility for any copies. | Annotations: yes with attribution. Images: depends on each source licence - not cleared as a whole | Public, via https://github.com/cvdfoundation/fashionpedia (see the site's *Download* page) | Annotations: yes with attribution. Images: do not assume you may |
| **DeepFashion** (https://mmlab.ie.cuhk.edu.hk/projects/DeepFashion.html) | Not an open licence: "non-commercial research purposes only" agreement | Obtained from the internet, **not** owned by MMLAB; some image data is encrypted | **No** | Signed *Release Agreement* emailed (institutional address) to the authors, who then send the password for the encrypted images. The *Category and Attribute Prediction* benchmark images don't need a password | **No.** You may not copy, publish or distribute any portion; internal copies at a single site of the same organisation are allowed |

Sources: Fashionpedia *Terms of Use* (https://fashionpedia.github.io/home/data_license.html) and the DeepFashion project page *Agreement* /
*Download Instructions* sections.

### What this means for WeatherFit AI
* **Bundled sample (`sample_dataset/`)** - 24 synthetic flat-colour drawings made by `make_sample_dataset.py`, released as **CC0**. No third-party
  rights are involved. This is the only dataset shipped with the project.
* **Fashionpedia** - fine for personal/research development after you accept the terms. Keep the attribution that the importer stores
  on every row (`attribution` column). Because image rights belong to the original photographers/sites, do not publish the imported
  crops, and think hard before using them in a commercial product - check each source's licence.
* **DeepFashion** - only if you (or your institution) have a signed agreement **and** your use is non-commercial research. The importer
  refuses to run without `--agreement-signed`. Never ship its images or derived data with an app.
* Neither dataset is used to *train* anything here. FashionCLIP is used only as a pretrained model for embeddings / zero-shot guesses.

## Importing (PowerShell, from the project folder, venv active)

```powershell
# 1) the bundled CC0 sample (safe, 24 items)
python dataset_import.py sample

# 2) Fashionpedia - you download it yourself first (annotation JSON + the image folder), then:
python dataset_import.py fashionpedia `
  --annotations "C:\data\fashionpedia\instances_attributes_val2020.json" `
  --images-dir  "C:\data\fashionpedia\val_test2020\test" `
  --limit 200 --accept-terms

# 3) DeepFashion (Category and Attribute Prediction files) - only with a signed agreement, non-commercial research
python dataset_import.py deepfashion --anno-dir "C:\data\DeepFashion\Anno_coarse" --images-dir "C:\data\DeepFashion" --limit 200 --agreement-signed

# remove everything imported from one source (your wardrobe is untouched)
python dataset_import.py remove fashionpedia
```
Re-running an import is safe: `(source, source_ref)` is unique, so duplicates are skipped and counted in the summary.

### How annotations map to the clothing schema
| Our column | Fashionpedia | DeepFashion |
|---|---|---|
| `category` | `categories[].name` via `FASHIONPEDIA_MAP` (e.g. "shirt, blouse" -> `shirt`, "pants" -> `trousers`, "shoe" -> `sneakers`). Garment *parts* (sleeve, collar...) and decorations are skipped | `list_category_cloth.txt` name via `DEEPFASHION_MAP` (e.g. "Tee" -> `t-shirt`, "Peacoat" -> `coat`) |
| image | the annotation's `bbox` crop | the whole image |
| `colour` | dominant colour of the crop (Fashionpedia has no colour attribute) | dominant colour of the image |
| `pattern` | first matching attribute name (stripe, check/plaid, floral, ...) else `solid` | `solid` (attribute files are not read) |
| `style`, `occasion`, `gender_presentation`, `season`, `fabric` | defaults (`Casual`, `casual`, `unisex`, `all`, `other`) - not annotated | same defaults |
| `license`, `attribution`, `source`, `source_ref` | filled from the dataset terms / annotation id | same |

The "shoe" mapping is lossy (Fashionpedia doesn't separate sneakers, loafers, boots), so shoe references are treated as generic footwear pictures.
