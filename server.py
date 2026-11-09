"""BYOK Cafe24 student chat; per-request key, no server-side key storage."""
import base64
import binascii
import json
import os
import re
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
# Each visitor supplies their own key in the X-Cafe24-Key header; never store it.
API_BASE = os.getenv('CAFE24_BASE_URL', 'https://llm-router.cafe24.com/api/v1').rstrip('/')
HOST = os.getenv('HOST', '0.0.0.0')
PORT = int(os.getenv('PORT', '8000'))
MAX_REQUEST = 14 * 1024 * 1024
MAX_IMAGES = 6
MAX_IMAGE_BYTES = 2_000_000
MAX_OUTPUT_TOKENS = 1100
FALLBACK_MODELS = [
    {'id': 'cafe24/auto', 'name': '자동 선택 (cafe24/auto)'},
    {'id': 'upstage/solar-mini4', 'name': 'Solar Mini 4 (upstage/solar-mini4)'},
]
# Optional manually verified prices, in KRW per 1,000,000 tokens.
# Never infer KRW prices from an unlabeled /models field.
PRICE_FILE = ROOT / 'model_prices.json'


def finite_price(value):
    if isinstance(value, bool):
        return None
    try:
        num = float(value)
        return num if math.isfinite(num) and num >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def manual_prices():
    try:
        mapping = json.loads(PRICE_FILE.read_text(encoding='utf-8'))
        return mapping if isinstance(mapping, dict) else {}
    except (OSError, ValueError):
        return {}


def extract_price(entry, overrides):
    """Return only explicitly KRW-denominated per-million-token prices.

    Model API field names are not guaranteed. Ambiguous pricing (USD, credits,
    per-token, unspecified currency/unit) must NOT be displayed as KRW.
    """
    model_id = entry['id']
    manually = overrides.get(model_id)
    if isinstance(manually, dict):
        prompt = finite_price(manually.get('input_krw_per_1m'))
        completion = finite_price(manually.get('output_krw_per_1m'))
        if prompt is not None and completion is not None:
            return {'input_krw_per_1m': prompt, 'output_krw_per_1m': completion, 'source': 'manual'}

    # Only explicit units are safe to interpret automatically.
    containers = [entry]
    for field in ('pricing', 'price', 'rates'):
        if isinstance(entry.get(field), dict):
            containers.append(entry[field])
    for container in containers:
        prompt = finite_price(container.get('input_price_per_1m_krw'))
        completion = finite_price(container.get('output_price_per_1m_krw'))
        if prompt is None or completion is None:
            prompt = finite_price(container.get('input_krw_per_1m'))
            completion = finite_price(container.get('output_krw_per_1m'))
        if prompt is not None and completion is not None:
            return {'input_krw_per_1m': prompt, 'output_krw_per_1m': completion, 'source': 'api'}
    return None


DATA_URL = re.compile(r'^data:(image/jpeg|image/png|image/webp);base64,([A-Za-z0-9+/=]+)$')


def valid_content(content):
    """Validate plain messages or OpenAI-compatible image_url content parts."""
    if isinstance(content, str):
        if not (1 <= len(content.strip()) <= 12000):
            raise ValueError('텍스트는 1~12,000자여야 합니다.')
        return content.strip()
    if not isinstance(content, list) or not (1 <= len(content) <= 10):
        raise ValueError('메시지 본문 형식이 올바르지 않습니다.')
    cleaned = []
    image_count = 0
    for part in content:
        if not isinstance(part, dict):
            raise ValueError('첨부 데이터 형식이 올바르지 않습니다.')
        if part.get('type') == 'text':
            text = part.get('text', '')
            if not isinstance(text, str) or len(text) > 12000:
                raise ValueError('텍스트는 12,000자 이하여야 합니다.')
            if text.strip():
                cleaned.append({'type': 'text', 'text': text.strip()})
        elif part.get('type') == 'image_url':
            obj = part.get('image_url', {})
            url = obj.get('url') if isinstance(obj, dict) else None
            match = DATA_URL.fullmatch(url) if isinstance(url, str) else None
            if not match:
                raise ValueError('JPEG/PNG/WebP base64 이미지 데이터만 지원합니다.')
            image_count += 1
            if image_count > MAX_IMAGES:
                raise ValueError(f'이미지는 한 메시지에 최대 {MAX_IMAGES}장입니다.')
            approx_size = len(match.group(2)) * 3 // 4
            if approx_size > MAX_IMAGE_BYTES + 4:
                raise ValueError('이미지당 2MB를 초과했습니다.')
            try:
                decoded = base64.b64decode(match.group(2), validate=True)
            except (binascii.Error, ValueError):
                raise ValueError('손상된 이미지 데이터입니다.')
            if len(decoded) > MAX_IMAGE_BYTES:
                raise ValueError('이미지당 2MB를 초과했습니다.')
            mime = match.group(1)
            signatures = {
                'image/jpeg': decoded.startswith(b'\xff\xd8\xff'),
                'image/png': decoded.startswith(b'\x89PNG\r\n\x1a\n'),
                'image/webp': decoded.startswith(b'RIFF') and decoded[8:12] == b'WEBP',
            }
            if not signatures[mime]:
                raise ValueError('파일 형식과 이미지 내용이 다릅니다.')
            cleaned.append({'type': 'image_url', 'image_url': {'url': url}})
        else:
            raise ValueError('지원하지 않는 첨부 유형입니다.')
    if not cleaned:
        raise ValueError('질문 또는 이미지를 입력하세요.')
    return cleaned


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        for key, value in [
            ('Content-Type', 'application/json; charset=utf-8'),
            ('Cache-Control', 'no-store'),
            ('Referrer-Policy', 'no-referrer'),
            ('X-Content-Type-Options', 'nosniff'),
            ('X-Frame-Options', 'DENY'),
            ('Content-Length', str(len(data))),
        ]:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def visitor_key(self):
        key = self.headers.get('X-Cafe24-Key', '').strip()
        if not key or len(key) > 1024 or '\r' in key or '\n' in key:
            return None
        return key

    def same_origin(self):
        # In browsers, reject requests from other sites; this is not user auth.
        origin = self.headers.get('Origin')
        if origin:
            from urllib.parse import urlsplit as parse
            parsed = parse(origin)
            host = self.headers.get('X-Forwarded-Host', self.headers.get('Host', ''))
            if parsed.scheme not in ('http', 'https') or parsed.netloc.lower() != host.lower():
                return False
        return self.headers.get('Sec-Fetch-Site', 'same-origin') in ('same-origin', 'none')

    def do_GET(self):
        route = urlsplit(self.path).path
        if route == '/':
            data = (ROOT / 'index.html').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if route == '/api/status':
            return self.respond(200, {'ready': True, 'mode': 'visitor-key'})
        if route == '/api/models':
            return self.respond(405, {'error': 'POST 요청을 이용하세요.'})
        return self.respond(404, {'error': '페이지를 찾을 수 없습니다.'})

    def models_for_key(self, key):
            if not key:
                return self.respond(503, {'error': 'Cafe24 API 키를 먼저 입력하세요.'})
            try:
                response = requests.get(API_BASE + '/models', headers={'Authorization': f'Bearer {key}'}, timeout=20)
                response.raise_for_status()
                payload = response.json()
                items = payload.get('data', []) if isinstance(payload, dict) else []
                if not isinstance(items, list):
                    items = []
                prices = manual_prices()
                models = []
                for entry in items:
                    if not isinstance(entry, dict) or not isinstance(entry.get('id'), str):
                        continue
                    mid = entry['id'].strip()
                    if not mid:
                        continue
                    display = entry.get('name') or entry.get('display_name') or mid
                    model = {'id': mid, 'name': str(display)}
                    price = extract_price(entry, prices)
                    if price:
                        model['price'] = price
                    models.append(model)
                unique = {m['id']: dict(m) for m in FALLBACK_MODELS}
                for model in models:
                    unique[model['id']] = model
                # Display manually configured prices for models missing in the API.
                for mid, model in unique.items():
                    if not model.get('price'):
                        manual = extract_price({'id': mid}, prices)
                        if manual:
                            model['price'] = manual
                return self.respond(200, {'models': list(unique.values()), 'source': 'api' if models else 'default', 'note': '' if models else '카페24 모델 목록이 비어 있어 기본 모델을 보여줍니다.'})
            except (requests.RequestException, ValueError):
                prices = manual_prices()
                fallback = []
                for entry in FALLBACK_MODELS:
                    model = dict(entry)
                    model_price = extract_price(model, prices)
                    if model_price:
                        model['price'] = model_price
                    fallback.append(model)
                return self.respond(200, {'models': fallback, 'source': 'default', 'note': '모델 목록을 불러오지 못했습니다. 모델 ID 직접 입력 기능을 사용하세요.'})

    def do_POST(self):
        route = urlsplit(self.path).path
        if route not in ('/api/chat', '/api/models'):
            return self.respond(404, {'error': 'API 경로를 찾을 수 없습니다.'})
        if not self.same_origin():
            return self.respond(403, {'error': '다른 사이트에서 보낸 요청은 허용하지 않습니다.'})
        key = self.visitor_key()
        if not key:
            return self.respond(401, {'error': '먼저 본인 Cafe24 API 키를 입력하세요.'})
        if route == '/api/models':
            return self.models_for_key(key)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= MAX_REQUEST:
                return self.respond(413, {'error': '요청 용량은 최대 14MB입니다. 이미지를 줄이세요.'})
            body = json.loads(self.rfile.read(size))
            model = body.get('model', 'cafe24/auto')
            messages = body.get('messages', [])
            if not isinstance(model, str) or not (1 <= len(model.strip()) <= 180):
                return self.respond(400, {'error': '모델 ID가 올바르지 않습니다.'})
            if not isinstance(messages, list) or not (1 <= len(messages) <= 30):
                return self.respond(400, {'error': '대화 메시지는 1~30개여야 합니다.'})
            cleaned = []
            for message in messages:
                if not isinstance(message, dict) or message.get('role') not in ('user', 'assistant'):
                    return self.respond(400, {'error': 'role은 user 또는 assistant여야 합니다.'})
                if message['role'] == 'assistant' and not isinstance(message.get('content'), str):
                    return self.respond(400, {'error': 'AI 답변 형식이 올바르지 않습니다.'})
                content = valid_content(message.get('content'))
                if message['role'] == 'assistant' and not isinstance(content, str):
                    return self.respond(400, {'error': 'AI 답변은 문자열이어야 합니다.'})
                cleaned.append({'role': message['role'], 'content': content})
            if cleaned[-1]['role'] != 'user':
                return self.respond(400, {'error': '마지막 메시지는 사용자 질문이어야 합니다.'})
            payload = {'model': model.strip(), 'messages': cleaned, 'max_tokens': MAX_OUTPUT_TOKENS}
            response = requests.post(
                API_BASE + '/chat/completions',
                json=payload,
                headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                timeout=150,
            )
            try:
                result = response.json()
            except ValueError:
                return self.respond(502, {'error': f'카페24에서 JSON 응답을 받지 못했습니다. HTTP {response.status_code}'})
            if not response.ok:
                err = result.get('error') if isinstance(result, dict) else None
                msg = err.get('message') if isinstance(err, dict) else (str(err) if err else 'API 요청 실패')
                return self.respond(response.status_code if 400 <= response.status_code < 600 else 502, {'error': str(msg)[:800], 'upstream_status': response.status_code})
            choices = result.get('choices') or []
            if not choices:
                return self.respond(502, {'error': 'AI 응답에서 choices를 찾지 못했습니다.'})
            content = choices[0].get('message', {}).get('content')
            if isinstance(content, list):
                content = '\n'.join(part.get('text', '') for part in content if isinstance(part, dict) and part.get('type') == 'text')
            if not isinstance(content, str):
                return self.respond(502, {'error': 'AI 응답을 텍스트로 표시할 수 없습니다.'})
            return self.respond(200, {
                'answer': content,
                'model': result.get('model', model),
                'usage': result.get('usage', {}),
                'finish_reason': choices[0].get('finish_reason'),
            })
        except (ValueError, TypeError) as exc:
            return self.respond(400, {'error': str(exc)[:500]})
        except requests.Timeout:
            return self.respond(504, {'error': '카페24 응답 시간이 초과되었습니다.'})
        except requests.RequestException:
            return self.respond(502, {'error': '카페24 서버와 통신하지 못했습니다.'})
        except Exception:
            return self.respond(500, {'error': '서버 처리 중 오류가 발생했습니다.'})


if __name__ == '__main__':
    print(f'Cafe24 AI Studio: http://{HOST}:{PORT}')
    print('종료: Ctrl+C | 사용자별 API 키 입력 방식')
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()