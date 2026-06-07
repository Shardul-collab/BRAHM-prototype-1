import re
import json
import os
import requests
from datetime import datetime


class LLMResponseError(Exception):
    pass


# ============================================================
# DEBUG LOGGING UTILITIES
# Unchanged from original.
# ============================================================

def _ensure_dirs():
    os.makedirs("logs/llm_payloads", exist_ok=True)
    os.makedirs("logs/llm_payloads_readable", exist_ok=True)


def _estimate_tokens(text: str) -> int:
    return int(len(text.split()) * 1.3)


def _log_payload(prompt, stage=None, extra=None):
    _ensure_dirs()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    stage = stage or "unknown"

    payload = {
        "stage": stage,
        "prompt": prompt,
        "extra": extra or {}
    }

    filepath = f"logs/llm_payloads/{stage}_{timestamp}.json"

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def _log_readable(prompt, stage=None):
    _ensure_dirs()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    stage = stage or "unknown"

    filepath = f"logs/llm_payloads_readable/{stage}_{timestamp}.txt"

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(prompt)


# ============================================================
# OLLAMA CLIENT — REST API
#
# FIX: Previous version used subprocess ["ollama", "run", ...]
# and attempted to pass --num-predict and --temperature as
# CLI flags. Ollama's CLI run command accepts NO generation
# flags — these are REST API parameters only. The subprocess
# call raised:
#   Error: unknown flag: --num-predict
#
# Fix: OllamaClient now calls the Ollama REST API directly
# via HTTP POST to http://localhost:11434/api/generate.
# This is the correct and documented way to control:
#   - num_predict (max output tokens)
#   - temperature
#   - any other generation options
#
# The REST API is always available when Ollama is running
# locally. No additional dependencies beyond `requests`
# which is already used throughout the codebase.
#
# stream=False is set so the response comes back as a single
# JSON object rather than a stream of chunks.
# ============================================================

OLLAMA_API_URL = os.environ.get("OLLAMA_API_URL", "http://localhost:11434/api/generate")


class OllamaClient:

    def __init__(self, model="mistral:7b-instruct", timeout=None):
        self.model = model
        self.timeout = timeout or 240

    def generate(self, prompt, max_tokens=800, temperature=0.7):

        payload = {
            "model":  self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature
            }
        }

        try:
            response = requests.post(
                OLLAMA_API_URL,
                json=payload,
                timeout=self.timeout
            )

        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                f"Cannot connect to Ollama at {OLLAMA_API_URL}. "
                "Is Ollama running? Start it with: ollama serve"
            )

        except requests.exceptions.Timeout:
            raise RuntimeError(
                f"Ollama request timed out after {self.timeout} seconds."
            )

        if response.status_code != 200:
            raise RuntimeError(
                f"Ollama API returned status {response.status_code}: "
                f"{response.text[:300]}"
            )

        try:
            data = response.json()
        except Exception:
            raise RuntimeError(
                f"Ollama returned non-JSON response: {response.text[:300]}"
            )

        output = data.get("response", "").strip()

        if not output:
            raise RuntimeError("Empty response from Ollama API.")

        return output


# ============================================================
# LLM SERVICE
# Unchanged from original except:
# - generate_text() default max_tokens 1000 → 800
# ============================================================

class LLMService:

    ALLOWED_CATEGORIES = {
        "material",
        "synthesis_method",
        "characterization",
        "application",
        "computational_method",
        "software",
        "exchange_correlation",
        "calculated_property",
        "defect_type",
        "doping_parameter",
        "annealing_condition",
        "optical_property",
        "electrical_property",
    }

    def __init__(self, llm_client):
        self.llm = llm_client

    # -----------------------------
    # JSON Knowledge Extraction — S5
    # -----------------------------
    def extract(self, prompt, stage="S5"):

        token_estimate = _estimate_tokens(prompt)
        print(f"[LLM DEBUG] Stage={stage} | Tokens≈{token_estimate}")

        if token_estimate > 8000:
            print(f"[WARNING] Large payload detected in {stage}: {token_estimate}")

        _log_payload(prompt, stage=stage, extra={"type": "extract"})
        _log_readable(prompt, stage=stage)

        try:
            raw = self.llm.generate(prompt)
        except Exception as e:
            raise LLMResponseError(f"LLM extraction failed: {e}")

        data = self._parse_json(raw)

        if data is None:
            raise LLMResponseError(
                f"Invalid JSON returned by LLM\nRaw output:\n{raw[:500]}"
            )

        return self._validate(data)

    # -----------------------------
    # Text Generation — legacy (S6/S7 removed)
    # -----------------------------
    def generate_text(self, prompt, max_tokens=800, temperature=0.5, stage="S6"):

        token_estimate = _estimate_tokens(prompt)
        print(f"[LLM DEBUG] Stage={stage} | Tokens≈{token_estimate}")

        if token_estimate > 8000:
            print(f"[WARNING] Large payload detected in {stage}: {token_estimate}")

        _log_payload(
            prompt,
            stage=stage,
            extra={
                "type": "generation",
                "max_tokens": max_tokens,
                "temperature": temperature
            }
        )
        _log_readable(prompt, stage=stage)

        try:
            output = self.llm.generate(
                prompt,
                max_tokens=max_tokens,
                temperature=temperature
            )

            return output.strip()

        except Exception as e:
            print(f"[LLM ERROR FULL] {repr(e)}")
            return None

    # -----------------------------
    # JSON Parsing with Recovery
    # -----------------------------
    def _parse_json(self, raw):

        try:
            return json.loads(raw)

        except Exception:
            match = re.search(r"\[.*\]", raw, re.DOTALL)

            if match:
                try:
                    return json.loads(match.group())
                except Exception:
                    return None

            return None

    # -----------------------------
    # Validate Extracted Knowledge
    # -----------------------------
    def _validate(self, data):

        if not isinstance(data, list):
            raise LLMResponseError("LLM output must be a list")

        validated = []

        for item in data:

            if "category" not in item or "value" not in item:
                raise LLMResponseError("Invalid knowledge format")

            category = item["category"]
            value    = item["value"]

            if category not in self.ALLOWED_CATEGORIES:
                continue

            validated.append({
                "category":     category,
                "value":        str(value),
                "section_source": "llm"
            })

        return validated


# ============================================================
# GEMINI CLIENT — Google AI Studio REST API
#
# Drop-in replacement for OllamaClient.
# Uses gemini-2.0-flash via the generateContent endpoint.
# API key is read from GOOGLE_API_KEY env var (set in .env).
# ============================================================

import os
from dotenv import load_dotenv

load_dotenv(
    dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"),
    override=False,
)

GEMINI_API_KEY   = os.environ.get("GOOGLE_API_KEY", "")
GEMINI_MODEL     = "gemini-2.0-flash"
GEMINI_API_URL   = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)


class GeminiClient:

    def __init__(self, model=GEMINI_MODEL, timeout=60):
        self.model   = model
        self.timeout = timeout
        if not GEMINI_API_KEY:
            raise RuntimeError(
                "GOOGLE_API_KEY is not set. "
                "Add it to agents/shani/.env"
            )

    def generate(self, prompt: str, max_tokens: int = 800, temperature: float = 0.7) -> str:

        payload = {
            "contents": [
                {"parts": [{"text": prompt}]}
            ],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature":     temperature,
            },
        }

        try:
            response = requests.post(
                GEMINI_API_URL,
                params={"key": GEMINI_API_KEY},
                json=payload,
                timeout=self.timeout,
            )
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                "Cannot connect to Gemini API. Check network."
            )
        except requests.exceptions.Timeout:
            raise RuntimeError(
                f"Gemini request timed out after {self.timeout}s."
            )

        if response.status_code != 200:
            raise RuntimeError(
                f"Gemini API error {response.status_code}: "
                f"{response.text[:300]}"
            )

        try:
            data = response.json()
            text = (
                data["candidates"][0]["content"]["parts"][0]["text"]
            ).strip()
        except (KeyError, IndexError, Exception) as exc:
            raise RuntimeError(
                f"Unexpected Gemini response structure: {exc}\n"
                f"Raw: {response.text[:300]}"
            )

        if not text:
            raise RuntimeError("Empty response from Gemini API.")

        return text


# ============================================================
# GEMINI CLIENT — Google AI Studio REST API
#
# Drop-in replacement for OllamaClient.
# Uses gemini-2.0-flash via the generateContent endpoint.
# API key is read from GOOGLE_API_KEY env var (set in .env).
# ============================================================

import os
from dotenv import load_dotenv

load_dotenv(
    dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"),
    override=False,
)

GEMINI_API_KEY   = os.environ.get("GOOGLE_API_KEY", "")
GEMINI_MODEL     = "gemini-2.0-flash"
GEMINI_API_URL   = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)


class GeminiClient:

    def __init__(self, model=GEMINI_MODEL, timeout=60):
        self.model   = model
        self.timeout = timeout
        if not GEMINI_API_KEY:
            raise RuntimeError(
                "GOOGLE_API_KEY is not set. "
                "Add it to agents/shani/.env"
            )

    def generate(self, prompt: str, max_tokens: int = 800, temperature: float = 0.7) -> str:

        payload = {
            "contents": [
                {"parts": [{"text": prompt}]}
            ],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature":     temperature,
            },
        }

        try:
            response = requests.post(
                GEMINI_API_URL,
                params={"key": GEMINI_API_KEY},
                json=payload,
                timeout=self.timeout,
            )
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                "Cannot connect to Gemini API. Check network."
            )
        except requests.exceptions.Timeout:
            raise RuntimeError(
                f"Gemini request timed out after {self.timeout}s."
            )

        if response.status_code != 200:
            raise RuntimeError(
                f"Gemini API error {response.status_code}: "
                f"{response.text[:300]}"
            )

        try:
            data = response.json()
            text = (
                data["candidates"][0]["content"]["parts"][0]["text"]
            ).strip()
        except (KeyError, IndexError, Exception) as exc:
            raise RuntimeError(
                f"Unexpected Gemini response structure: {exc}\n"
                f"Raw: {response.text[:300]}"
            )

        if not text:
            raise RuntimeError("Empty response from Gemini API.")

        return text


# ============================================================
# GEMINI CLIENT — Google AI Studio REST API
#
# Drop-in replacement for OllamaClient.
# Uses gemini-2.0-flash via the generateContent endpoint.
# API key is read from GOOGLE_API_KEY env var (set in .env).
# ============================================================

import os
from dotenv import load_dotenv

load_dotenv(
    dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"),
    override=False,
)

GEMINI_API_KEY   = os.environ.get("GOOGLE_API_KEY", "")
GEMINI_MODEL     = "gemini-2.0-flash"
GEMINI_API_URL   = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)


class GeminiClient:

    def __init__(self, model=GEMINI_MODEL, timeout=60):
        self.model   = model
        self.timeout = timeout
        if not GEMINI_API_KEY:
            raise RuntimeError(
                "GOOGLE_API_KEY is not set. "
                "Add it to agents/shani/.env"
            )

    def generate(self, prompt: str, max_tokens: int = 800, temperature: float = 0.7) -> str:

        payload = {
            "contents": [
                {"parts": [{"text": prompt}]}
            ],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature":     temperature,
            },
        }

        try:
            response = requests.post(
                GEMINI_API_URL,
                params={"key": GEMINI_API_KEY},
                json=payload,
                timeout=self.timeout,
            )
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                "Cannot connect to Gemini API. Check network."
            )
        except requests.exceptions.Timeout:
            raise RuntimeError(
                f"Gemini request timed out after {self.timeout}s."
            )

        if response.status_code != 200:
            raise RuntimeError(
                f"Gemini API error {response.status_code}: "
                f"{response.text[:300]}"
            )

        try:
            data = response.json()
            text = (
                data["candidates"][0]["content"]["parts"][0]["text"]
            ).strip()
        except (KeyError, IndexError, Exception) as exc:
            raise RuntimeError(
                f"Unexpected Gemini response structure: {exc}\n"
                f"Raw: {response.text[:300]}"
            )

        if not text:
            raise RuntimeError("Empty response from Gemini API.")

        return text


# ============================================================
# CEREBRAS CLIENT — OpenAI-compatible REST API
# Model: llama-3.3-70b
# Key: CEREBRAS_API_KEY in agents/shani/.env
# ============================================================

CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "")
CEREBRAS_API_URL = "https://api.cerebras.ai/v1/chat/completions"
CEREBRAS_MODEL   = "gpt-oss-120b"


class CerebrasClient:

    def __init__(self, model=CEREBRAS_MODEL, timeout=60):
        self.model   = model
        self.timeout = timeout
        if not CEREBRAS_API_KEY:
            raise RuntimeError(
                "CEREBRAS_API_KEY is not set. "
                "Add it to agents/shani/.env"
            )

    def generate(self, prompt: str, max_tokens: int = 800, temperature: float = 0.7) -> str:

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        try:
            response = requests.post(
                CEREBRAS_API_URL,
                headers={
                    "Authorization": f"Bearer {CEREBRAS_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
            )
        except requests.exceptions.ConnectionError:
            raise RuntimeError("Cannot connect to Cerebras API. Check network.")
        except requests.exceptions.Timeout:
            raise RuntimeError(f"Cerebras request timed out after {self.timeout}s.")

        if response.status_code != 200:
            raise RuntimeError(
                f"Cerebras API error {response.status_code}: {response.text[:300]}"
            )

        try:
            msg = response.json()["choices"][0]["message"]
            text = (msg.get("content") or msg.get("reasoning") or "").strip()
        except (KeyError, IndexError, Exception) as exc:
            raise RuntimeError(
                f"Unexpected Cerebras response: {exc}\nRaw: {response.text[:300]}"
            )

        if not text:
            raise RuntimeError("Empty response from Cerebras API.")

        return text


# ============================================================
# GROQ CLIENT — OpenAI-compatible REST API
# S5 model:  llama-3.1-8b-instant  (840 TPS, fast extraction)
#
# Key rotation + retry logic:
# - Reads GROQ_API_KEY and GROQ_API_KEY_2 from env
# - On 429: reads Retry-After header (or defaults to 12s),
#   sleeps, rotates to next key, retries
# - Max MAX_RETRIES attempts cycling through available keys
# - Keys: GROQ_API_KEY, GROQ_API_KEY_2 in agents/shani/.env
# ============================================================

import time as _time

GROQ_API_URL  = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL_S5 = "llama-3.1-8b-instant"

# Build key pool — skip empty strings so a missing key 2 is fine
_GROQ_KEYS = [
    k for k in [
        os.environ.get("GROQ_API_KEY",   ""),
        os.environ.get("GROQ_API_KEY_2", ""),
    ]
    if k
]


class GroqClient:

    MAX_RETRIES      = 6      # total attempts across all keys
    DEFAULT_WAIT_S   = 12     # fallback sleep when Retry-After header absent

    def __init__(self, model=GROQ_MODEL_S5, timeout=60):
        self.model   = model
        self.timeout = timeout
        if not _GROQ_KEYS:
            raise RuntimeError(
                "No Groq API keys found. "
                "Set GROQ_API_KEY (and optionally GROQ_API_KEY_2) "
                "in agents/shani/.env"
            )
        self._key_index = 0   # start with first key

    def _current_key(self):
        return _GROQ_KEYS[self._key_index % len(_GROQ_KEYS)]

    def _rotate_key(self):
        self._key_index = (self._key_index + 1) % len(_GROQ_KEYS)

    def generate(self, prompt: str, max_tokens: int = 800, temperature: float = 0.7) -> str:

        payload = {
            "model":       self.model,
            "messages":    [{"role": "user", "content": prompt}],
            "max_tokens":  max_tokens,
            "temperature": temperature,
        }

        last_error = None

        for attempt in range(self.MAX_RETRIES):

            key = self._current_key()

            try:
                response = requests.post(
                    GROQ_API_URL,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type":  "application/json",
                    },
                    json=payload,
                    timeout=self.timeout,
                )
            except requests.exceptions.ConnectionError:
                raise RuntimeError("Cannot connect to Groq API. Check network.")
            except requests.exceptions.Timeout:
                raise RuntimeError(f"Groq request timed out after {self.timeout}s.")

            # ── Success ──────────────────────────────────────────
            if response.status_code == 200:
                try:
                    text = response.json()["choices"][0]["message"]["content"].strip()
                except (KeyError, IndexError, Exception) as exc:
                    raise RuntimeError(
                        f"Unexpected Groq response: {exc}\nRaw: {response.text[:300]}"
                    )
                if not text:
                    raise RuntimeError("Empty response from Groq API.")
                return text

            # ── Rate limit (429) — rotate key and sleep ──────────
            if response.status_code == 429:
                wait = self.DEFAULT_WAIT_S
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait = max(int(retry_after), 1)
                    except ValueError:
                        pass

                key_label = f"key{self._key_index + 1}"
                self._rotate_key()
                next_label = f"key{self._key_index + 1}"

                print(
                    f"[GROQ] 429 on {key_label} (attempt {attempt + 1}/{self.MAX_RETRIES}). "
                    f"Sleeping {wait}s then retrying with {next_label}."
                )
                _time.sleep(wait)
                last_error = f"429 rate limit after {self.MAX_RETRIES} attempts"
                continue

            # ── Other HTTP errors — fail immediately ──────────────
            raise RuntimeError(
                f"Groq API error {response.status_code}: {response.text[:300]}"
            )

        # All retries exhausted
        raise RuntimeError(f"Groq API: {last_error}")