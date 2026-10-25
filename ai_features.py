"""Optional Gemini and FashionCLIP features. API keys are read only from environment/.env/Streamlit secrets.

Every Gemini request goes through ``gemini_guard.run_guarded`` which switches Gemini off when ENABLE_GEMINI=false, caches repeated
requests, never retries quota (429) errors, pauses Gemini after one, and retries only temporary errors with back-off.
All failures surface as ``gemini_guard.GeminiUnavailable`` (a RuntimeError with a beginner-friendly message).
"""
from __future__ import annotations
import io, os
from functools import lru_cache
from typing import Any, Callable
from PIL import Image
from dotenv import load_dotenv
load_dotenv()

import config
from gemini_guard import GeminiUnavailable, run_guarded, text_cache_key


def _gemini_client(key: str, timeout_s: float = 30):
    """Build a client with a request timeout and NO hidden SDK retries (retries are controlled in gemini_guard)."""
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise GeminiUnavailable("sdk_missing", "The Gemini SDK is not installed. Run: pip install -r requirements.txt") from exc
    ms = int(timeout_s * 1000)
    for make_options in (lambda: types.HttpOptions(timeout=ms, retry_options=types.HttpRetryOptions(attempts=1)),
                         lambda: types.HttpOptions(timeout=ms)):
        try:
            return genai.Client(api_key=key, http_options=make_options()), types
        except (TypeError, ValueError, AttributeError):   # older SDKs without these options
            continue
    return genai.Client(api_key=key), types


def gemini_text(prompt: str, image_bytes: bytes | None = None, mime_type: str = "image/jpeg",
                system_instruction: str | None = None, timeout_s: float | None = None, max_retries: int | None = None) -> str:
    """Ask Gemini for text (optionally about one image). Raises GeminiUnavailable when Gemini is off, paused, out of quota or failing.

    Identical requests within GEMINI_CACHE_MINUTES are answered from memory without calling the API."""
    key = config.get_str("GEMINI_API_KEY")
    timeout = timeout_s if timeout_s is not None else config.get_float("GEMINI_TIMEOUT_SECONDS", 30.0, minimum=5.0)

    def call(model: str) -> str:
        client, types = _gemini_client(key, timeout)
        contents: Any = prompt
        if image_bytes:
            contents = [prompt, types.Part.from_bytes(data=image_bytes, mime_type=mime_type)]
        cfg = types.GenerateContentConfig(system_instruction=system_instruction) if system_instruction else None
        response = client.models.generate_content(model=model, contents=contents, config=cfg)
        answer = getattr(response, "text", None)
        if not answer:
            raise RuntimeError("Gemini returned no text. Please try again.")
        return answer

    answer, _info = run_guarded("text", call, cache_key=text_cache_key(prompt, system_instruction, image_bytes, mime_type), max_retries=max_retries)
    return answer


def make_llm(max_retries: int | None = None) -> Callable[[str, str], str] | None:
    """(prompt, system) -> text callable for the chat/stylist/voice code, or None when Gemini is off or has no key."""
    if not config.gemini_api_key():
        return None
    return lambda prompt, system: gemini_text(prompt, system_instruction=system, max_retries=max_retries)


def generate_outfit_image(prompt: str, reference_images: list[bytes] | None = None) -> bytes:
    """Generate a fashion concept image with Gemini's image model (GEMINI_IMAGE_MODEL, separate from the text model).

    ``reference_images`` (optional JPEG/PNG bytes, e.g. the user's own clothing photos) are sent alongside the prompt so the
    model can use them as visual references. The model may still alter colours/patterns; it is a rendering, not a photo.
    Image results are never cached (each press of the button is a deliberate request)."""
    key = config.get_str("GEMINI_API_KEY")

    def call(model: str) -> bytes:
        client, types = _gemini_client(key, max(60.0, config.get_float("GEMINI_TIMEOUT_SECONDS", 30.0)))
        contents: Any = prompt
        if reference_images:
            contents = [prompt] + [types.Part.from_bytes(data=b, mime_type="image/jpeg") for b in reference_images[:5]]
        response = client.models.generate_content(model=model, contents=contents,
                                                  config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]))
        for part in (getattr(response, "parts", None) or []):
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                return inline.data
        raise RuntimeError("The image model did not return an image. Check model access/quota and try again.")

    data, _info = run_guarded("image", call)
    return data


def list_gemini_models() -> list[str]:
    """Names of Gemini models your key can use for generateContent (an explicit, user-pressed check; it does not use generation quota).

    Raises GeminiUnavailable if Gemini is off or the request fails."""
    if not config.gemini_enabled():
        raise GeminiUnavailable("disabled", "Gemini is turned off (ENABLE_GEMINI=false).")
    key = config.get_str("GEMINI_API_KEY")
    if not key:
        raise GeminiUnavailable("no_key", "Add GEMINI_API_KEY to your local .env file first.")
    from gemini_guard import classify_exception
    try:
        client, _types = _gemini_client(key, 20)
        names: list[str] = []
        for m in client.models.list():
            actions = getattr(m, "supported_actions", None)
            if actions is not None and "generateContent" not in actions:
                continue
            name = str(getattr(m, "name", "")).replace("models/", "")
            if name.startswith("gemini"):
                names.append(name)
        return sorted(set(names))
    except GeminiUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        kind, _, _ = classify_exception(exc)
        from security import redact
        raise GeminiUnavailable(kind, f"Could not list models ({redact(exc)[:120]}).", kind == "transient") from None


LABEL_PROMPT = (
    "You label ONE clothing item photo for a wardrobe app. Look only at the clothing item. Reply with a single JSON object and nothing else, "
    "using exactly these keys: category, colour, secondary_colour, pattern, fabric, season, style, occasion, gender_presentation. "
    "Allowed values - category: {categories}; pattern: {patterns}; fabric: {fabrics}; season: any of {seasons} (comma separated); "
    "style: one of {styles}; occasion: any of {occasions} (comma separated); gender_presentation: men, women or unisex. "
    "colour/secondary_colour: a simple colour name such as {colours} (secondary_colour may be empty). If unsure of a value use an empty string. "
    "Do not describe people and do not guess identity."
)


def parse_label_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model reply (tolerates ```json fences / extra prose); raises ValueError if none."""
    import json
    import re
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("The AI reply did not contain label data.")
    data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("The AI label reply was not an object.")
    return {str(k): (v if isinstance(v, (str, int, float)) else ",".join(map(str, v)) if isinstance(v, list) else "") for k, v in data.items()}


def gemini_label_item(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict[str, Any]:
    """Ask Gemini vision to suggest wardrobe labels for one clothing photo (suggestions only - the user confirms)."""
    import taxonomy as tx
    prompt = LABEL_PROMPT.format(categories=", ".join(sorted(tx.CATEGORIES)), patterns=", ".join(tx.PATTERNS), fabrics=", ".join(tx.FABRICS),
                                 seasons=", ".join(tx.SEASONS), styles=", ".join(tx.STYLES), occasions=", ".join(tx.OCCASION_TAGS),
                                 colours=", ".join(tx.COLOURS[:12]))
    return parse_label_json(gemini_text(prompt, image_bytes, mime_type))


def fashionclip_category(image_bytes: bytes) -> str | None:
    """Zero-shot category guess with the pretrained FashionCLIP model (no training on your wardrobe)."""
    import taxonomy as tx
    cats = [c for c in tx.CATEGORIES if tx.slot_of(c) != "accessory"] + ["watch", "sunglasses", "cap", "backpack", "umbrella"]
    ranked = fashionclip_rank(image_bytes, [f"a photo of a {c}" for c in cats])
    return ranked[0][0].replace("a photo of a ", "") if ranked else None


def fashionclip_embed_images(images: list[bytes]) -> list[list[float]]:
    """Image embeddings from pretrained FashionCLIP (used for similarity search; nothing is trained or fine-tuned)."""
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("FashionCLIP packages are not installed. See requirements-fashionclip.txt and README.md.") from exc
    processor, model = _load_fashionclip("patrickjohncyh/fashion-clip")
    pil = [Image.open(io.BytesIO(b)).convert("RGB") for b in images]
    inputs = processor(images=pil, return_tensors="pt")
    device = next(model.parameters()).device
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        feats = model.get_image_features(**inputs)
    return [[float(x) for x in row] for row in feats.detach().cpu().tolist()]


def fashionclip_embed_text(text: str) -> list[float]:
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("FashionCLIP packages are not installed. See requirements-fashionclip.txt and README.md.") from exc
    processor, model = _load_fashionclip("patrickjohncyh/fashion-clip")
    inputs = processor(text=[text], return_tensors="pt", padding=True)
    device = next(model.parameters()).device
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        feats = model.get_text_features(**inputs)
    return [float(x) for x in feats[0].detach().cpu().tolist()]


def fashionclip_rank(image_bytes: bytes, labels: list[str]) -> list[tuple[str, float]]:
    """Optional FashionCLIP ranking based on the supplied notebook's model ID."""
    try:
        import torch
        from transformers import AutoProcessor, AutoModelForZeroShotImageClassification
    except ImportError as exc:
        raise RuntimeError("FashionCLIP packages are not installed. See requirements-fashionclip.txt and README.md.") from exc
    model_id = "patrickjohncyh/fashion-clip"
    processor = _load_fashionclip(model_id)
    model = processor[1]
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    inputs = processor[0](text=labels, images=image, return_tensors="pt", padding=True)
    device = next(model.parameters()).device
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        outputs = model(**inputs)
        scores = outputs.logits_per_image[0].softmax(dim=0).detach().cpu().tolist()
    return sorted(zip(labels, [float(s) for s in scores]), key=lambda x: x[1], reverse=True)


def fashionclip_rank_images(text: str, images: list[bytes]) -> list[float]:
    """Score how well each image matches ``text`` using FashionCLIP (higher = better match).

    Returns raw image-text similarity logits, one per image, in input order. Use them for ranking only.
    """
    if not images:
        return []
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("FashionCLIP packages are not installed. See requirements-fashionclip.txt and README.md.") from exc
    try:
        processor, model = _load_fashionclip("patrickjohncyh/fashion-clip")
    except ImportError as exc:
        raise RuntimeError("FashionCLIP packages are not installed. See requirements-fashionclip.txt and README.md.") from exc
    pil = [Image.open(io.BytesIO(b)).convert("RGB") for b in images]
    inputs = processor(text=[text], images=pil, return_tensors="pt", padding=True)
    device = next(model.parameters()).device
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        logits = model(**inputs).logits_per_text[0]
    return [float(x) for x in logits.detach().cpu().tolist()]


@lru_cache(maxsize=1)
def _load_fashionclip(model_id: str):
    """Load once per process (first call downloads the model)."""
    from transformers import AutoProcessor, AutoModelForZeroShotImageClassification
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    processor = AutoProcessor.from_pretrained(model_id)
    model = AutoModelForZeroShotImageClassification.from_pretrained(model_id).to(device)
    model.eval()
    return processor, model
