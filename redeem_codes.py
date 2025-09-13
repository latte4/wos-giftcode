#!/usr/bin/env python3
# Whiteout Gift Code Redeemer Script Version 3.0.0
# Refactored to provide 3 different options for OCR

import os
import warnings
import requests
import time
import random
import hashlib
import json
import csv
import argparse
import sys
import base64
import cv2
import re
import numpy as np
from datetime import datetime, timedelta
from glob import glob
from PIL import Image, ImageEnhance, ImageFilter

try:
    import colorama
    colorama.init(autoreset=True)       # Automatically resets color after each print
    C_RESET = colorama.Style.RESET_ALL
    C_INFO = colorama.Fore.CYAN         # Blue/Cyan for general info
    C_PROCESS = colorama.Fore.BLUE      # Slightly different blue for processing steps
    C_SUCCESS = colorama.Fore.GREEN
    C_ERROR = colorama.Fore.RED
    C_WARN = colorama.Fore.YELLOW
    C_OCR = colorama.Fore.MAGENTA       # Purple/Magenta for OCR results
    C_DIM = colorama.Style.DIM          # Dim for less important details like fetch attempts
except ImportError:
    print("警告: coloramaライブラリが見つかりません。ログ出力は色付けされません。")
    C_RESET = C_INFO = C_PROCESS = C_SUCCESS = C_ERROR = C_WARN = C_OCR = C_DIM = ""

# Potentially Add CaptchaCracker
try:
    import CaptchaCracker as cc
    CAPTCHA_CRACKER_AVAILABLE = True
except ImportError:
    cc = None
    CAPTCHA_CRACKER_AVAILABLE = False

# Potentially Add EasyOCR
try:
    import easyocr
    EASYOCR_AVAILABLE = True
except ImportError:
    easyocr = None
    EASYOCR_AVAILABLE = False

# Potentially Add ddddocr
try:
    import ddddocr
    DDDDOCR_AVAILABLE = True
except ImportError:
    ddddocr = None
    DDDDOCR_AVAILABLE = False

warnings.filterwarnings("ignore", message=".*pin_memory.*", category=UserWarning)

# Global Configuration
LOGIN_URL = "https://wos-giftcode-api.centurygame.com/api/player"
CAPTCHA_URL = "https://wos-giftcode-api.centurygame.com/api/captcha"
REDEEM_URL = "https://wos-giftcode-api.centurygame.com/api/gift_code"
WOS_ENCRYPT_KEY = "tB87#kPtkxqOS2"

DELAY = 1
RETRY_DELAY = 2
MAX_RETRIES = 3
CAPTCHA_RETRIES = 4
CAPTCHA_SLEEP = 60
MAX_CAPTCHA_FETCH_ATTEMPTS = 4

# Captcha/OCR Common Configuration
EXPECTED_CAPTCHA_LENGTH = 4
VALID_CHARACTERS = set('123456789ABCDEFGHIJKLMNPQRSTUVWXYZ')

# CaptchaCracker Configuration
CC_WEIGHTS_PATH = "model/weights.h5"
CC_IMG_WIDTH = 150
CC_IMG_HEIGHT = 40
CC_MAX_LENGTH = EXPECTED_CAPTCHA_LENGTH
CC_CHARACTERS = VALID_CHARACTERS

# EasyOCR Configuration
MIN_CONFIDENCE_EASYOCR = 0.4

# Argument parsing
def parse_args():
    parser = argparse.ArgumentParser(description="Redeem gift codes with optional OCR settings and code sources")

    # Dynamically build OCR choices based on available libraries
    ocr_choices = []
    available_methods = []
    if DDDDOCR_AVAILABLE:
        ocr_choices.append('ddddocr')
        available_methods.append('DdddOcr')
    if EASYOCR_AVAILABLE:
        ocr_choices.append('easyocr')
        available_methods.append('EasyOCR')
    if CAPTCHA_CRACKER_AVAILABLE:
        ocr_choices.append('captchacracker')
        available_methods.append('CaptchaCracker')

    default_ocr = 'ddddocr'
    if ocr_choices:
        # Prioritize: ddddocr -> CaptchaCracker -> EasyOCR
        if 'ddddocr' in ocr_choices: default_ocr = 'ddddocr'
        elif 'captchacracker' in ocr_choices: default_ocr = 'captchacracker'
        elif 'easyocr' in ocr_choices: default_ocr = 'easyocr'

    if not EASYOCR_AVAILABLE: print("警告: EasyOCRライブラリが見つかりません。")
    if not CAPTCHA_CRACKER_AVAILABLE: print("警告: CaptchaCrackerライブラリが見つかりません。")
    if not DDDDOCR_AVAILABLE: print("警告: ddddocrライブラリが見つかりません。")

    if not ocr_choices:
        print("致命的なエラー: OCRライブラリ (EasyOCR, CaptchaCracker, または ddddocr) が見つかりません。少なくとも1つインストールしてください。")
        sys.exit(1)

    parser.add_argument('--ocr-method', type=str, default=default_ocr, choices=ocr_choices,
                        help=f'OCR method to use. Available: {", ".join(ocr_choices)}. (Default: {default_ocr})')
    parser.add_argument('--use-gpu', type=int, nargs='?', const=0, default=None,
                        help='Enable GPU. EasyOCR: PyTorch device ID (0, 1,...). CaptchaCracker: TensorFlow (often automatic). ddddocr: ONNX Runtime (can use CUDA/DirectML, often automatic). Specify ID mainly for EasyOCR.')
    parser.add_argument('--csv', type=str, help='Path to CSV file or directory containing FIDs')
    parser.add_argument('--code', type=str, required=True, help='Single code to redeem (REQUIRED)')
    parser.add_argument('--save-images', type=int, default=0, choices=[0, 1, 2, 3],
                        help='Image saving mode: 0=None (default), 1=Failed CAPTCHA only, 2=Successful CAPTCHA only, 3=All (Success/Failed)')
    return parser.parse_args()

args = parse_args()

# --- Basic Sanity Checks ---
if args.ocr_method == 'easyocr' and not EASYOCR_AVAILABLE:
    print("エラー: EasyOCRが選択されましたが、利用できません。")
    sys.exit(1)
if args.ocr_method == 'captchacracker' and not CAPTCHA_CRACKER_AVAILABLE:
    print("エラー: CaptchaCrackerが選択されましたが、利用できません。")
    sys.exit(1)
if args.ocr_method == 'ddddocr' and not DDDDOCR_AVAILABLE:
    print("エラー: ddddocrが選択されましたが、利用できません。")
    sys.exit(1)

script_dir = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(script_dir, "redeemed_codes.txt")
CAPTCHA_SAVE_DIR = os.path.join(script_dir, "captcha_images")

try:
    os.makedirs(CAPTCHA_SAVE_DIR, exist_ok=True)
    rel = os.path.relpath(CAPTCHA_SAVE_DIR, script_dir)
    print(f"CAPTCHA画像用のディレクトリを作成しました: {rel}")
except Exception as e:
    print(f"エラー: ディレクトリ '{CAPTCHA_SAVE_DIR}' の作成に失敗しました: {str(e)}")
# --- Initialize OCR Readers ---
easyocr_reader = None
cc_apply_model = None
ddddocr_ocr = None

# Initialize EasyOCR if needed
if args.ocr_method == 'easyocr' and EASYOCR_AVAILABLE:
    use_easyocr_gpu = False
    if args.use_gpu is not None:
        try:
            import torch
            if torch.cuda.is_available():
                num_gpus = torch.cuda.device_count()
                gpu_id = args.use_gpu
                if gpu_id < 0 or gpu_id >= num_gpus:
                    print(f"EasyOCR: 無効なGPUデバイスID {gpu_id}。利用可能なデバイス: 0..{num_gpus-1}。CPUにフォールバックします。")
                else:
                    torch.cuda.set_device(gpu_id)
                    gpu_name = torch.cuda.get_device_name(gpu_id)
                    print(f"EasyOCR: GPUデバイス {gpu_id}: {gpu_name} を使用しようとしています。")
                    use_easyocr_gpu = True
            else:
                print("EasyOCR: GPUが要求されましたが、PyTorchでCUDAデバイスが見つかりませんでした。")
        except Exception as e:
            print(f"EasyOCR: GPUデバイス {args.use_gpu} の設定/チェック中にエラーが発生しました: {str(e)}。CPUにフォールバックします。")

    print(f"EasyOCR: リーダーを初期化しています (GPU={use_easyocr_gpu})...")
    try:
        easyocr_reader = easyocr.Reader(['en'], gpu=use_easyocr_gpu)
        print(f"EasyOCR: リーダーは {'GPU' if use_easyocr_gpu else 'CPU'} で初期化されました。")
    except Exception as e:
        print(f"EasyOCR: 初期化に失敗しました: {str(e)}。EasyOCRは利用できません。")
        EASYOCR_AVAILABLE = False
        if args.ocr_method == 'easyocr':
            print("致命的なエラー: 選択されたEasyOCRの初期化に失敗しました。終了します。")
            sys.exit(1)

# Initialize CaptchaCracker if needed
if args.ocr_method == 'captchacracker' and CAPTCHA_CRACKER_AVAILABLE:
    print("CaptchaCracker: ApplyModelを初期化しています...")
    try:
        import tensorflow as tf
        gpu_devices = tf.config.list_physical_devices('GPU')
        if gpu_devices:
            print(f"CaptchaCracker (TensorFlow): GPUデバイスが見つかりました: {gpu_devices}")
        else:
            print("CaptchaCracker (TensorFlow): GPUが見つかりませんでした。CPUを使用します。")
    except ImportError:
        print("CaptchaCracker: TensorFlowが見つかりません (CaptchaCrackerと一緒にインストールされているはずです)。")
    except Exception as tf_err:
        print(f"CaptchaCracker: TensorFlow GPUのチェック中にエラーが発生しました: {tf_err}")

    # Actual ApplyModel initialization
    if not os.path.exists(CC_WEIGHTS_PATH):
        print(f"致命的なエラー: CaptchaCrackerの重みファイルが \'{CC_WEIGHTS_PATH}\' に見つかりません。スクリプトに対するパスが正しいことを確認してください。")
        CAPTCHA_CRACKER_AVAILABLE = False
        if args.ocr_method == 'captchacracker':
            print("必要なCaptchaCrackerモデルが見つからないため終了します。")
            sys.exit(1)

    if CAPTCHA_CRACKER_AVAILABLE:
        try:
            cc_apply_model = cc.ApplyModel(
                    weights_path=CC_WEIGHTS_PATH,
                    img_width=CC_IMG_WIDTH,
                    img_height=CC_IMG_HEIGHT,
                    max_length=CC_MAX_LENGTH,
                    characters=CC_CHARACTERS
            )
            print(f"CaptchaCracker: ApplyModelが {CC_WEIGHTS_PATH} から正常にロードされました。")
        except Exception as e:
            print(f"致命的なエラー: CaptchaCracker ApplyModelの初期化に失敗しました: {e}")
            print("重みパス、寸法、max_length、および文字設定を確認してください。")
            CAPTCHA_CRACKER_AVAILABLE = False
            if args.ocr_method == 'captchacracker':
                print("CaptchaCrackerの初期化に失敗したため終了します。")
                sys.exit(1)

# Initialize ddddocr if needed
if args.ocr_method == 'ddddocr' and DDDDOCR_AVAILABLE:
    print("DdddOcr: 初期化中...")
    try:
        ddddocr_ocr = ddddocr.DdddOcr(ocr=True, det=False, show_ad=False)
        print("DdddOcr: 正常に初期化されました。")
        # try:
        #     providers = ddddocr_ocr.onnx_session.get_providers()
        #     print(f"DdddOcr (ONNX Runtime): 利用可能な実行プロバイダー: {providers}")
        #     if \'CUDAExecutionProvider\' in providers:
        #         print("  -> CUDA (GPU) がONNX Runtimeで利用可能です。")
        #     elif \'DmlExecutionProvider\' in providers:
        #         print("  -> DirectML (GPU) がONNX Runtimeで利用可能です。")
        #     elif \'CoreMLExecutionProvider\' in providers:
        #         print("  -> CoreML (Apple Silicon) がONNX Runtimeで利用可能です。")
        # except Exception as onnx_e:
        #     print(f"DdddOcr: ONNX Runtimeプロバイダーをクエリできませんでした: {onnx_e}")

    except Exception as e:
        print(f"致命的なエラー: DdddOcrの初期化に失敗しました: {e}")
        DDDDOCR_AVAILABLE = False
        if args.ocr_method == 'ddddocr':
            print("ddddocrの初期化に失敗したため終了します。")
            sys.exit(1)

RESULT_MESSAGES = {
    "SUCCESS": "正常に引き換えられました",
    "RECEIVED": "すでに引き換え済みです",
    "SAME TYPE EXCHANGE": "正常に引き換えられました（同タイプ）",
    "TIME ERROR": "コードの有効期限が切れています",
    "TIMEOUT RETRY": "サーバーが再試行を要求しました",
    "USED": "引き換え上限に達しました",
    "Server requested retry": "サーバーが再試行を要求しました",
    "CAPTCHA CHECK ERROR": "CAPTCHAチェックエラー（サーバー検証失敗）",
    "CAPTCHA CHECK TOO FREQUENT": "CAPTCHAチェック頻度が高すぎます（レート制限）",
    "Sign Error": "署名エラー（リクエストエンコードの問題）",
    "NOT LOGIN": "ログインしていません / セッションの有効期限が切れました",
}

counters = {
    "success": 0,
    "already_redeemed": 0,
    "errors": 0,
    "captcha_fetch_attempts": 0,        # Total calls to CAPTCHA_URL
    "captcha_ocr_attempts": 0,          # Total times OCR was performed (per method call)
    "captcha_ocr_success": 0,           # OCR produced a valid format code (any method)
    "captcha_ocr_success_cc": 0,        # Successful CaptchaCracker OCR
    "captcha_ocr_success_easyocr": 0,   # Successful EasyOCR
    "captcha_ocr_success_ddddocr": 0,   # Successful ddddocr OCR
    "captcha_redeem_success": 0,        # Captcha passed server validation
    "captcha_redeem_failure": 0,        # Captcha failed server validation
    "captcha_rate_limited": 0,          # Hit rate limit during fetch or redeem
}

error_details = {} # Stores FID -> last error message

script_start_time = time.time()

def preprocess_captcha_for_easyocr(image_np):
    """Apply multiple preprocessing techniques tailored for EasyOCR"""
    processed_images = []

    # Method 0: Basic threshold
    if len(image_np.shape) > 2 and image_np.shape[2] == 3:
        gray = cv2.cvtColor(image_np, cv2.COLOR_BGR2GRAY)
    elif len(image_np.shape) == 2:
        gray = image_np
    else:
        print("警告: 前処理で予期しない画像形状です。")
        return [] # Return empty list if input is weird

    # Method 1: Grayscale
    processed_images.append(("Original_Grayscale", gray))
    
    # Method 2: Adaptive threshold
    adaptive_thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2)
    processed_images.append(("Adaptive Threshold", adaptive_thresh))
    
    # Method 3: Otsu's thresholding
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    processed_images.append(("Otsu Threshold", otsu))
    
    # Method 4: Noise removal
    denoised = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
    processed_images.append(("Denoised", denoised))
    
    # Method 5: Noise removal + threshold
    _, denoised_thresh = cv2.threshold(denoised, 127, 255, cv2.THRESH_BINARY)
    processed_images.append(("Denoised+Threshold", denoised_thresh))
    
    # Method 6: Dilated
    kernel = np.ones((2,2), np.uint8)
    dilated = cv2.dilate(gray, kernel, iterations=1)
    processed_images.append(("Dilated", dilated))
    
    # Method 7: Eroded
    eroded = cv2.erode(gray, kernel, iterations=1)
    processed_images.append(("Eroded", eroded))
    
    # Method 8: Edge enhancement
    edges = cv2.Canny(gray, 100, 200)
    processed_images.append(("Edges", edges))
    
    # Method 9: Morphological operations
    kernel = np.ones((1,1), np.uint8)
    opening = cv2.morphologyEx(gray, cv2.MORPH_OPEN, kernel)
    processed_images.append(("Opening", opening))
    
    # Method 10: Enhanced contrast
    if isinstance(image_np, Image.Image):
        pil_img = image_np
    else:
        if len(image_np.shape) > 2 and image_np.shape[2] == 3:
            pil_img = Image.fromarray(cv2.cvtColor(image_np, cv2.COLOR_BGR2RGB))
        else:
            pil_img = Image.fromarray(image_np)
            
    enhanced = ImageEnhance.Contrast(pil_img).enhance(2.0)
    enhanced_np = np.array(enhanced)
    if len(enhanced_np.shape) > 2 and enhanced_np.shape[2] == 3:
        enhanced_np = cv2.cvtColor(enhanced_np, cv2.COLOR_RGB2BGR)
    processed_images.append(("Enhanced Contrast", enhanced_np))
    
    # Method 11: Sharpened
    sharpened = pil_img.filter(ImageFilter.SHARPEN)
    sharpened_np = np.array(sharpened)
    if len(sharpened_np.shape) > 2 and sharpened_np.shape[2] == 3:
        sharpened_np = cv2.cvtColor(sharpened_np, cv2.COLOR_RGB2BGR)
    processed_images.append(("Sharpened", sharpened_np))
    
    # Method 12: Color filtering (for captchas with specific color text)
    if len(image_np.shape) > 2 and image_np.shape[2] == 3:
        # Extract blue channel
        blue_channel = image_np[:, :, 0]
        _, blue_thresh = cv2.threshold(blue_channel, 127, 255, cv2.THRESH_BINARY)
        processed_images.append(("Blue Channel", blue_thresh))
        
        # Create an HSV version and filter for common captcha colors
        hsv = cv2.cvtColor(image_np, cv2.COLOR_BGR2HSV)
        
        # Purple-blue range
        lower_purple = np.array([100, 50, 50])
        upper_purple = np.array([170, 255, 255])
        purple_mask = cv2.inRange(hsv, lower_purple, upper_purple)
        processed_images.append(("Purple Filter", purple_mask))
        
        # Green range
        lower_green = np.array([40, 50, 50])
        upper_green = np.array([90, 255, 255])
        green_mask = cv2.inRange(hsv, lower_green, upper_green)
        processed_images.append(("Green Filter", green_mask))

    return processed_images

def save_captcha_image_final(temp_path, final_filename_base, log_prefix):
    if not temp_path or not os.path.exists(temp_path) or not final_filename_base:
        return

    try:
        safe_base = re.sub(r'[\\/*?:"<>|]', "", final_filename_base)
        base, ext = os.path.splitext(safe_base)
        if not ext: ext = ".png"

        final_filename = f"{base}{ext}"
        final_path = os.path.join(CAPTCHA_SAVE_DIR, final_filename)
        counter = 1
        while os.path.exists(final_path) and counter <= 100:
            final_filename = f"{base}_{counter}{ext}"
            final_path = os.path.join(CAPTCHA_SAVE_DIR, final_filename)
            counter += 1

        if counter > 100:
            log(f"Warning: Could not find unique filename for {safe_base} after 100 tries. Discarding image.")
            final_path = None

        if final_path:
            os.rename(temp_path, final_path)
            log(f"{log_prefix}: Saved captcha image as {os.path.basename(final_path)}")
        else:
            try: os.remove(temp_path)
            except OSError: pass

    except OSError as rename_err:
        log(f"Error renaming/saving captcha {os.path.basename(temp_path)} to {os.path.basename(final_path) if final_path else 'N/A'}: {rename_err}")
        try: os.remove(temp_path)
        except OSError: pass
    except Exception as e:
        log(f"Unexpected error saving captcha image {os.path.basename(temp_path)}: {e}")
        try: os.remove(temp_path)
        except OSError: pass

def solve_captcha_with_captchacracker(image_bytes):
    """Attempts to solve captcha using the CaptchaCracker model."""
    if not cc_apply_model or not CAPTCHA_CRACKER_AVAILABLE:
        log("CaptchaCracker model not available or not initialized.")
        return None

    counters["captcha_ocr_attempts"] += 1
    try:
        predicted_text = cc_apply_model.predict_from_bytes(image_bytes)
        if predicted_text and isinstance(predicted_text, str) and len(predicted_text) == EXPECTED_CAPTCHA_LENGTH and all(c in VALID_CHARACTERS for c in predicted_text):
            log(f"CaptchaCracker Result: {predicted_text}")
            counters["captcha_ocr_success_cc"] += 1
            return predicted_text
        else:
            log(f"CaptchaCracker produced invalid result: '{predicted_text}' (Len: {len(predicted_text) if predicted_text else 0}, Expected: {EXPECTED_CAPTCHA_LENGTH}, Chars OK: {all(c in VALID_CHARACTERS for c in predicted_text) if predicted_text else 'N/A'})")
            return None
    except Exception as e:
        log(f"CaptchaCracker prediction error: {e}")
        return None

def solve_captcha_with_easyocr(image_np):
    """Attempts to solve captcha using EasyOCR with preprocessing."""
    if not easyocr_reader or not EASYOCR_AVAILABLE:
        log("EasyOCR reader not available or not initialized.")
        return None, "None"

    counters["captcha_ocr_attempts"] += 1
    processed_images = preprocess_captcha_for_easyocr(image_np)
    candidates = []
    processed_methods_tried = set()

    original_method_name = "Original_BGR"
    try:
        results = easyocr_reader.readtext(image_np, detail=1, allowlist=''.join(sorted(list(VALID_CHARACTERS))))
        processed_methods_tried.add(original_method_name)
        for result in results:
            if len(result) >= 3:
                text = result[1].strip().replace(' ', '')
                confidence = result[2]
                if text and confidence > MIN_CONFIDENCE_EASYOCR and all(c in VALID_CHARACTERS for c in text):
                    candidates.append((text, confidence, original_method_name))
    except Exception as ocr_err:
        log(f"EasyOCR Error (Method: {original_method_name}): {ocr_err}")

    for method_name, processed_img in processed_images:
        if method_name in processed_methods_tried: continue

        try:
            if len(processed_img.shape) == 2:
                ocr_input = cv2.cvtColor(processed_img, cv2.COLOR_GRAY2BGR)
            elif len(processed_img.shape) == 3 and processed_img.shape[2] == 3:
                ocr_input = processed_img
            else:
                log(f"Warning: Skipping unsupported image format for EasyOCR (Method: {method_name}, Shape: {processed_img.shape})")
                continue

            results = easyocr_reader.readtext(ocr_input, detail=1, allowlist=''.join(sorted(list(VALID_CHARACTERS))))
            processed_methods_tried.add(method_name)

            for result in results:
                if len(result) >= 3:
                    text = result[1].strip().replace(' ', '')
                    confidence = result[2]
                    if text and confidence > MIN_CONFIDENCE_EASYOCR and all(c in VALID_CHARACTERS for c in text):
                        candidates.append((text, confidence, method_name))
        except Exception as ocr_err:
            log(f"EasyOCR Error (Method: {method_name}): {ocr_err}")

    candidates.sort(key=lambda x: (len(x[0]) == EXPECTED_CAPTCHA_LENGTH, x[1]), reverse=True)

    best_result_text = None
    best_confidence = 0.0
    best_method = "None"

    for text, confidence, method_name in candidates:
        if len(text) == EXPECTED_CAPTCHA_LENGTH:
            best_result_text = text
            best_confidence = confidence
            best_method = method_name
            log(f"EasyOCR Best Candidate: {best_result_text} (Method: {best_method}, Conf: {best_confidence:.2f})")
            counters["captcha_ocr_success_easyocr"] += 1
            return best_result_text, best_method 

    if candidates:
        log(f"EasyOCR Warning: No valid {EXPECTED_CAPTCHA_LENGTH}-char result found. Best candidate: '{candidates[0][0]}' (Method: {candidates[0][2]}, Conf: {candidates[0][1]:.2f}, Len: {len(candidates[0][0])})")
    else:
        log("EasyOCR failed to find any valid candidates after preprocessing.")

    return None, "None"

def solve_captcha_with_ddddocr(image_bytes):
    """Attempts to solve captcha using the ddddocr library."""
    if not ddddocr_ocr or not DDDDOCR_AVAILABLE:
        log("DdddOcr not available or not initialized.")
        return None

    counters["captcha_ocr_attempts"] += 1
    try:
        predicted_text = ddddocr_ocr.classification(image_bytes)

        if predicted_text and isinstance(predicted_text, str):
            predicted_text = predicted_text.upper()
            
            if len(predicted_text) == EXPECTED_CAPTCHA_LENGTH and all(c in VALID_CHARACTERS for c in predicted_text):
                log(f"DdddOcr Result: {predicted_text}")
                counters["captcha_ocr_success_ddddocr"] += 1
                return predicted_text
            else:
                log(f"DdddOcr produced invalid result: '{predicted_text}' (Len: {len(predicted_text) if predicted_text else 0}, Expected: {EXPECTED_CAPTCHA_LENGTH}, Chars OK: {all(c in VALID_CHARACTERS for c in predicted_text) if predicted_text else 'N/A'})")
                return None
        else:
            log(f"DdddOcr produced invalid result: '{predicted_text}' (Type: {type(predicted_text)})")
            return None
    except Exception as e:
        log(f"DdddOcr classification error: {e}")
        return None

def fetch_and_solve_captcha(fid, nickname, retry_queue=None):
    """ Fetches captcha and solves using the configured OCR method. """
    if retry_queue is None: retry_queue = {}

    attempts = 0
    current_time = time.time()
    temp_image_path = None
    solved_code = None

    while attempts < MAX_CAPTCHA_FETCH_ATTEMPTS:
        counters["captcha_fetch_attempts"] += 1

        if temp_image_path and os.path.exists(temp_image_path):
            try: os.remove(temp_image_path); temp_image_path = None
            except OSError as e: log(f"Warning: Failed to clean up previous temp file {os.path.basename(temp_image_path)}: {e}")

        payload = encode_data({"fid": fid, "time": int(time.time() * 1000), "init": "0"})
        response = make_request(CAPTCHA_URL, payload)

        if response and response.status_code == 200:
            try:
                captcha_data = response.json()
                if captcha_data.get("code") == 1 and "TOO FREQUENT" in captcha_data.get("msg", "").upper():
                    log(f"{nickname} ({fid}) - [Attempt {attempts+1}/{MAX_CAPTCHA_FETCH_ATTEMPTS}] Captcha fetch rate limited, adding to retry queue...", level='warn')
                    retry_queue[fid] = current_time + CAPTCHA_SLEEP
                    counters["captcha_rate_limited"] += 1
                    return None, None, retry_queue, "RateLimited"

                if "data" in captcha_data and "img" in captcha_data["data"]:
                    img_field = captcha_data["data"]["img"]
                    if isinstance(img_field, str) and img_field.startswith("data:image"):
                        try: img_base64 = img_field.split(",", 1)[1]
                        except IndexError: log(f"{nickname}({fid}) - [Attempt {attempts+1}/{MAX_CAPTCHA_FETCH_ATTEMPTS}] Malformed data URL received.", level='error'); img_base64 = None
                    elif isinstance(img_field, str):
                        img_base64 = img_field
                    else:
                        log(f"{nickname} ({fid}) - [Attempt {attempts+1}/{MAX_CAPTCHA_FETCH_ATTEMPTS}] Unexpected image data format: {type(img_field)}", level='error'); img_base64 = None

                    if not img_base64:
                        attempts += 1; time.sleep(random.uniform(1.0, 2.0)); continue

                    try:
                        img_bytes = base64.b64decode(img_base64)
                    except base64.binascii.Error as b64_err:
                        log(f"{nickname} ({fid}) - [Attempt {attempts+1}/{MAX_CAPTCHA_FETCH_ATTEMPTS}] Error decoding base64 captcha image: {b64_err}", level='error')
                        attempts += 1; time.sleep(random.uniform(1.0, 2.0)); continue

                    img_np = None
                    if args.ocr_method == 'easyocr' or args.ocr_method == 'both' or args.save_images > 0:
                        try:
                            nparr = np.frombuffer(img_bytes, np.uint8)
                            img_np = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                            if img_np is None:
                                log(f"{nickname} ({fid}) - [Attempt {attempts+1}/{MAX_CAPTCHA_FETCH_ATTEMPTS}] Error: Failed to decode captcha image data using OpenCV.", level='error')
                        except Exception as decode_err:
                            log(f"{nickname} ({fid}) - [Attempt {attempts+1}/{MAX_CAPTCHA_FETCH_ATTEMPTS}] Error decoding image with OpenCV: {decode_err}", level='error')

                    if args.save_images > 0 and img_np is not None:
                        timestamp = int(time.time())
                        safe_fid = re.sub(r'\W+', '', str(fid))
                        temp_filename = f"temp_fid{safe_fid}_fetch{attempts+1}_{timestamp}.png"
                        temp_image_path = os.path.join(CAPTCHA_SAVE_DIR, temp_filename)
                        try:
                            if not cv2.imwrite(temp_image_path, img_np):
                                log(f"{nickname} ({fid}) - [Attempt {attempts+1}/{MAX_CAPTCHA_FETCH_ATTEMPTS}] Error: Failed to write temporary captcha image: {temp_image_path}", level='error'); temp_image_path = None
                        except Exception as save_err:
                            log(f"{nickname} ({fid}) - [Attempt {attempts+1}/{MAX_CAPTCHA_FETCH_ATTEMPTS}] Error saving temporary captcha image to {temp_image_path}: {save_err}", level='error')
                    elif args.save_images > 0 and img_np is None:
                        log(f"{nickname} ({fid}) - [Attempt {attempts+1}/{MAX_CAPTCHA_FETCH_ATTEMPTS}] Cannot save temp image as decoding failed.", level='warn')

                    solved_code = None
                    ocr_method_successful = "None"

                    # DdddOcr
                    if not solved_code and args.ocr_method == 'ddddocr':
                        log(f"{nickname} ({fid}) - [Attempt {attempts+1}/{MAX_CAPTCHA_FETCH_ATTEMPTS}] Attempting OCR with ddddocr...", level='process')
                        ddddocr_code = solve_captcha_with_ddddocr(img_bytes)
                        if ddddocr_code:
                            solved_code = ddddocr_code
                            ocr_method_successful = "DdddOcr"

                    # CaptchaCracker
                    elif args.ocr_method == 'captchacracker':
                        log(f"{nickname} ({fid}) - [Attempt {attempts+1}/{MAX_CAPTCHA_FETCH_ATTEMPTS}] Attempting OCR with CaptchaCracker...", level='process')
                        solved_code = solve_captcha_with_captchacracker(img_bytes)
                        if solved_code:
                            ocr_method_successful = "CaptchaCracker"

                    # EasyOCR
                    elif args.ocr_method == 'easyocr':
                        if img_np is not None:
                            log(f"{nickname} ({fid}) - [Attempt {attempts+1}/{MAX_CAPTCHA_FETCH_ATTEMPTS}] Attempting OCR with EasyOCR...", level='process')
                            easyocr_code, easyocr_method_name = solve_captcha_with_easyocr(img_np)
                            if easyocr_code:
                                solved_code = easyocr_code
                                ocr_method_successful = f"EasyOCR ({easyocr_method_name})"
                        else:
                            log(f"{nickname} ({fid}) - [Attempt {attempts+1}/{MAX_CAPTCHA_FETCH_ATTEMPTS}] Skipping EasyOCR because image decoding failed.", level='warn')

                    # Check OCR Result
                    if solved_code:
                        log(f"{nickname} ({fid}) - [Attempt {attempts+1}/{MAX_CAPTCHA_FETCH_ATTEMPTS}] OCR successful using {ocr_method_successful}. Solved: {C_OCR}{solved_code}", level='success')
                        counters["captcha_ocr_success"] += 1
                        return solved_code, temp_image_path, retry_queue, ocr_method_successful
                    else:
                        log(f"{nickname} ({fid}) - [Attempt {attempts+1}/{MAX_CAPTCHA_FETCH_ATTEMPTS}] OCR failed using method '{args.ocr_method}'. Refetching...", level='warn')
                        if temp_image_path and os.path.exists(temp_image_path) and args.save_images not in [1, 3]:
                            try: os.remove(temp_image_path); temp_image_path = None
                            except OSError: pass
                else:
                    log(f"{nickname} ({fid}) - [Attempt {attempts+1}/{MAX_CAPTCHA_FETCH_ATTEMPTS}] Captcha image/data missing in API response: {captcha_data.get('msg', 'No message')}. Refetching...", level='warn')

            except json.JSONDecodeError:
                log(f"{nickname} ({fid}) - [Attempt {attempts+1}/{MAX_CAPTCHA_FETCH_ATTEMPTS}] Error decoding JSON response from CAPTCHA_URL. Response: {response.text[:200]}", level='error')
            except Exception as e:
                log(f"{nickname} ({fid}) - [Attempt {attempts+1}/{MAX_CAPTCHA_FETCH_ATTEMPTS}] Error processing captcha: {str(e.__class__.__name__)} - {str(e)}", level='error')

        elif response is not None:
            log(f"{nickname} ({fid}) - [Attempt {attempts+1}/{MAX_CAPTCHA_FETCH_ATTEMPTS}] Failed to fetch captcha (HTTP {response.status_code}). Retrying...", level='warn')
        else:
            log(f"{nickname} ({fid}) - [Attempt {attempts+1}/{MAX_CAPTCHA_FETCH_ATTEMPTS}] Failed to fetch captcha (Network Error). Retrying...", level='warn')

        attempts += 1
        if attempts < MAX_CAPTCHA_FETCH_ATTEMPTS:
            time.sleep(random.uniform(1.5, 3.0))

    log(f"{nickname} ({fid}) - Failed to fetch/solve captcha after {attempts} attempts. Adding to retry queue.", level='error')
    retry_queue[fid] = current_time + CAPTCHA_SLEEP

    if temp_image_path and os.path.exists(temp_image_path):
        if args.save_images in [1, 3]:
            save_captcha_image_final(temp_image_path, f"FAIL_OCR_{fid}_{int(time.time())}", f"{nickname}({fid}) - Captcha Fail OCR/Fetch")
        else:
            try: os.remove(temp_image_path)
            except OSError as e: log(f"Warning: Failed to clean up final temp file {os.path.basename(temp_image_path)} after failure: {e}")
        temp_image_path = None

    return None, None, retry_queue, "Fetch/Solve Failed"

def log(message, level='info', to_file=True):
    """Logs a message to console with color and optionally to a file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    color_prefix = ""
    if level == 'success':
        color_prefix = C_SUCCESS
    elif level == 'error':
        color_prefix = C_ERROR
    elif level == 'warn':
        color_prefix = C_WARN
    elif level == 'ocr':
        color_prefix = C_OCR
    elif level == 'process':
        color_prefix = C_PROCESS
    elif level == 'info':
         color_prefix = C_INFO
    elif level == 'dim':
         color_prefix = C_DIM

    console_log_entry = f"{C_DIM}{timestamp}{C_RESET} - {color_prefix}{message}{C_RESET}"
    
    try:
        print(console_log_entry)
    except UnicodeEncodeError:
        cleaned = console_log_entry.encode('utf-8', errors='replace').decode('ascii', errors='replace')
        print(cleaned)

    if to_file:
        file_log_entry = f"{timestamp} - {message}"
        try:
            log_dir = os.path.dirname(LOG_FILE)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir)

            with open(LOG_FILE, "a", encoding="utf-8-sig", newline='') as f:
                f.write(file_log_entry + "\n")
        except Exception as e:
            error_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"{C_DIM}{error_timestamp}{C_RESET} - {C_ERROR}LOGGING ERROR to file {LOG_FILE}: {str(e)}{C_RESET}")

def encode_data(data):
    secret = WOS_ENCRYPT_KEY
    sorted_keys = sorted(data.keys())
    encoded_data = "&".join([f"{key}={json.dumps(data[key]) if isinstance(data[key], dict) else data[key]}" for key in sorted_keys])
    return {"sign": hashlib.md5(f"{encoded_data}{secret}".encode()).hexdigest(), **data}

def make_request(url, payload, headers=None):
    session = requests.Session()
    # Basic headers that might help avoid suspicion
    base_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    if headers:
        base_headers.update(headers)

    for attempt in range(MAX_RETRIES):
        try:
            response = session.post(url, data=payload, headers=base_headers, timeout=15)
            if response.status_code in [502, 503, 504]:
                log(f"Request Attempt {attempt+1} to {url.split('/')[-1]} failed: HTTP {response.status_code} (Server Error/Overload). Retrying...")
                time.sleep(RETRY_DELAY * (attempt + 1) * 1.5)
                continue
            elif response.status_code == 429:
                log(f"Request Attempt {attempt+1} to {url.split('/')[-1]} failed: HTTP 429 (Rate Limited). Retrying after longer delay...")
                time.sleep(RETRY_DELAY * (attempt + 1) * 2)
                continue
            elif response.status_code != 200:
                log(f"Request Attempt {attempt+1} to {url.split('/')[-1]} failed: HTTP {response.status_code}, Response: {response.text[:150]}")
            else:
                return response

        except requests.exceptions.Timeout:
            log(f"Request Attempt {attempt+1} to {url.split('/')[-1]} timed out. Retrying...")
        except requests.exceptions.ConnectionError as e:
            log(f"Request Attempt {attempt+1} to {url.split('/')[-1]} failed: Connection Error {str(e)}. Retrying...")
        except requests.exceptions.RequestException as e:
            log(f"Request Attempt {attempt+1} to {url.split('/')[-1]} failed: {str(e.__class__.__name__)} - {str(e)}")

        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAY * (attempt + 1))

    return None

def redeem_gift_code(fid, cdk, retry_queue=None):
    """Redeems a gift code, handling captcha and saving the image based on result."""
    if retry_queue is None: retry_queue = {}

    if not str(fid).strip().isdigit():
        log(f"Skipping invalid FID format: '{fid}'")
        return {"msg": "Invalid FID format"}, retry_queue
    fid = str(fid).strip()

    current_time = time.time()
    if fid in retry_queue and retry_queue[fid] > current_time:
        return {"msg": "In cooldown"}, retry_queue

    captcha_code_sent = None
    temp_image_path_final = None
    final_redeem_data = {"msg": "Processing Error"}
    ocr_method_succeeded = "None"
    
    # --- Login Step ---
    try:
        login_payload = encode_data({"fid": fid, "time": int(time.time() * 1000)})
        login_resp = make_request(LOGIN_URL, login_payload)
        if not login_resp:
            log(f"Login request failed for FID {fid} after retries.")
            return {"msg": "Login request failed"}, retry_queue
        try:
            login_data = login_resp.json()
            if login_data.get("code") != 0:
                login_msg = login_data.get('msg', 'Unknown login error')
                log(f"{nickname} ({fid}) - Login failed: Code {login_data.get('code')}, Msg: {login_msg}", level='error')
                return {"msg": f"Login failed: {login_msg}"}, retry_queue
        except json.JSONDecodeError:
            log(f"{fid} - Error decoding login response JSON. Response: {login_resp.text[:200]}", level='error')
            return {"msg": "Login JSON Decode Error"}, retry_queue

        nickname = login_data.get("data", {}).get("nickname", "Unknown Player")
        server_id = login_data.get("data", {}).get("server_id", "???") # Example: Extract more info if available
        fid_index = all_player_ids.index(fid) + 1 if 'all_player_ids' in globals() and fid in all_player_ids else '?'
        total_fids = len(all_player_ids) if 'all_player_ids' in globals() else '?'
        log(f"Processing S{server_id}-{nickname}({fid}) [{fid_index}/{total_fids}] for code: {args.code}", level='info')

    except Exception as login_err:
        log(f"{fid} - Unexpected error during login phase: {login_err}", level='error')
        return {"msg": f"Unexpected Login Error: {login_err}"}, retry_queue

    # --- Captcha Fetch, Solve, and Redeem Loop ---
    for attempt in range(CAPTCHA_RETRIES):
        captcha_code_sent = None
        temp_image_path_attempt = None

        # Clean up image from previous attempt
        if temp_image_path_final and os.path.exists(temp_image_path_final):
            try: os.remove(temp_image_path_final); temp_image_path_final = None
            except OSError: pass

        # 1. Fetch and Solve Captcha
        captcha_code_sent, temp_image_path_attempt, retry_queue, ocr_method_succeeded = fetch_and_solve_captcha(fid, nickname, retry_queue)

        if captcha_code_sent is None:
            if ocr_method_succeeded == "RateLimited":
                final_redeem_data = {"msg": "CAPTCHA CHECK TOO FREQUENT"}
            else:
                final_redeem_data = {"msg": "Captcha fetch/solve failed"}
            temp_image_path_final = temp_image_path_attempt
            break

        temp_image_path_final = temp_image_path_attempt

        # 2. Attempt Redemption with the solved code
        try:
            redeem_payload = encode_data({
                "fid": fid, "cdk": cdk, "captcha_code": captcha_code_sent,
                "time": int(time.time() * 1000)
            })
            redeem_resp = make_request(REDEEM_URL, redeem_payload)

            if not redeem_resp:
                log(f"{nickname} ({fid}) - [Attempt {attempt+1}/{CAPTCHA_RETRIES}] Redemption request failed (no response), retrying fetch/solve...", level='warn')
                if attempt < CAPTCHA_RETRIES - 1: time.sleep(RETRY_DELAY); continue
                else: final_redeem_data = {"msg": "Redemption request failed"}; break

            # 3. Process Redemption Response
            try:
                final_redeem_data = redeem_resp.json()
                msg = final_redeem_data.get('msg', 'Unknown error').strip('.')
                err_code = final_redeem_data.get('err_code')

                log(f"{nickname} ({fid}) - [Attempt {attempt+1}/{CAPTCHA_RETRIES}] Server Response: Code {final_redeem_data.get('code', 'N/A')}, Msg '{msg}'", level='dim')

                is_captcha_check_error = (msg == "CAPTCHA CHECK ERROR" or err_code == 40103)
                is_captcha_rate_limit = (msg == "CAPTCHA CHECK TOO FREQUENT" or err_code == 40104)
                is_sign_error = (msg == "Sign Error" or err_code == 40001)
                is_server_retry_request = (msg in ["Server requested retry", "TIMEOUT RETRY"])
                is_not_logged_in = (msg == "NOT LOGIN" or err_code == 40101)

                if is_captcha_check_error:
                    log(f"{nickname} ({fid}) - [Attempt {attempt+1}/{CAPTCHA_RETRIES}] Redemption failed: Captcha Check Error (Server). Retrying fetch/solve...", level='warn')
                    counters["captcha_redeem_failure"] += 1
                    if args.save_images in [1, 3] and temp_image_path_attempt and os.path.exists(temp_image_path_attempt):
                        save_captcha_image_final(temp_image_path_attempt, f"FAIL_{fid}_{int(time.time())}", f"{nickname}({fid}) - Captcha Fail Server ({ocr_method_succeeded} -> {msg})")
                        temp_image_path_attempt = None
                    if attempt < CAPTCHA_RETRIES - 1: time.sleep(random.uniform(2.0, 3.5)); continue
                    else: log(f"{nickname} ({fid}) - Max redemption attempts reached after Captcha Check Error.", level='error'); break

                elif is_captcha_rate_limit:
                    log(f"{nickname} ({fid}) - [Attempt {attempt+1}/{CAPTCHA_RETRIES}] Redemption rate limited, adding to retry queue...", level='warn')
                    retry_queue[fid] = time.time() + CAPTCHA_SLEEP
                    counters["captcha_rate_limited"] += 1
                    counters["captcha_redeem_failure"] += 1
                    if args.save_images in [1, 3] and temp_image_path_attempt and os.path.exists(temp_image_path_attempt):
                        save_captcha_image_final(temp_image_path_attempt, f"FAIL_{fid}_{int(time.time())}", f"{nickname}({fid}) - Captcha Rate Limited ({ocr_method_succeeded} -> {msg})")
                        temp_image_path_attempt = None
                    break

                elif is_sign_error or is_server_retry_request:
                    log(f"{nickname} ({fid}) - [Attempt {attempt+1}/{CAPTCHA_RETRIES}] Redemption failed ({msg}). Retrying fetch/solve...", level='warn')
                    if args.save_images in [1, 3] and temp_image_path_attempt and os.path.exists(temp_image_path_attempt):
                        save_captcha_image_final(temp_image_path_attempt, f"FAIL_{fid}_{int(time.time())}", f"{nickname}({fid}) - Captcha Fail ({ocr_method_succeeded} -> {msg})")
                        temp_image_path_attempt = None
                    if attempt < CAPTCHA_RETRIES - 1: time.sleep(RETRY_DELAY); continue
                    else: log(f"{nickname} ({fid}) - Max redemption attempts reached after {msg}.", level='error'); break

                elif is_not_logged_in:
                    log(f"{nickname} ({fid}) - [Attempt {attempt+1}/{CAPTCHA_RETRIES}] Session expired or invalid. Skipping FID.", level='error')
                    break

                else:
                    if not is_captcha_check_error and not is_captcha_rate_limit:
                        counters["captcha_redeem_success"] += 1
                    is_redeem_success_or_done = msg in ["SUCCESS", "RECEIVED", "SAME TYPE EXCHANGE"]
                    if is_redeem_success_or_done and args.save_images in [2, 3] and temp_image_path_attempt and os.path.exists(temp_image_path_attempt):
                        save_captcha_image_final(temp_image_path_attempt, f"{captcha_code_sent}", f"{nickname}({fid}) - Captcha OK ({ocr_method_succeeded})")
                        temp_image_path_attempt = None
                    break

            except json.JSONDecodeError:
                log(f"{nickname} ({fid}) - [Attempt {attempt+1}/{CAPTCHA_RETRIES}] Error decoding redemption response JSON. Response: {redeem_resp.text[:200]}", level='error')
                final_redeem_data = {"msg": "Redemption JSON Decode Error"}
                break

        except Exception as e:
            log(f"{nickname} ({fid}) - [Attempt {attempt+1}/{CAPTCHA_RETRIES}] Unexpected error during redeem: {str(e.__class__.__name__)} - {str(e)}", level='error')
            final_redeem_data = {"msg": f"Unexpected Error Attempt {attempt+1}: {str(e)}"}
            break

    if temp_image_path_attempt and os.path.exists(temp_image_path_attempt):
        try:
            os.remove(temp_image_path_attempt)
        except OSError as del_err:
            log(f"Error deleting unhandled temp image {os.path.basename(temp_image_path_attempt)}: {del_err}", level='warn')

    # Log the final result before returning
    raw_msg = final_redeem_data.get('msg', 'Unknown error').strip('.')
    friendly_msg = RESULT_MESSAGES.get(raw_msg, raw_msg)

    is_queued_for_retry = fid in retry_queue and retry_queue[fid] > time.time()
    is_final_state = not is_queued_for_retry
    log_suffix = ""
    if is_queued_for_retry:
        log_suffix = f" (Queued for retry in ~{int(retry_queue[fid] - time.time())}s)"

    log_level = 'info'
    if is_final_state:
        if raw_msg in ["SUCCESS", "SAME TYPE EXCHANGE"]:
            log_level = 'success'
        elif raw_msg == "RECEIVED":
            log_level = 'dim'
        elif raw_msg in ["TIME ERROR", "USED"]:
            log_level = 'warn'
        elif raw_msg in ["Invalid FID format", "Login JSON Decode Error", "Login request failed"]:
             log_level = 'error'
        elif raw_msg not in ["In cooldown", "Captcha fetch/solve failed", "CAPTCHA CHECK TOO FREQUENT"]:
             log_level = 'error'
    elif is_queued_for_retry:
        log_level = 'warn'

    log(f"{nickname}({fid}) - Result: {friendly_msg}{log_suffix}", level=log_level)

    return final_redeem_data, retry_queue

def read_player_ids_from_csv(file_path):
    player_ids = []
    format_detected = "unknown"
    encodings_to_try = ['utf-8-sig', 'utf-8', 'latin-1', 'gbk']

    for encoding in encodings_to_try:
        try:
            with open(file_path, mode="r", newline="", encoding=encoding) as file:
                content = file.read()
                if ',' in content.splitlines()[0]:
                    format_detected = "comma-separated"
                elif content.strip():
                    format_detected = "newline"
                else:
                    format_detected = "empty"
                    log(f"Warning: File '{os.path.basename(file_path)}' appears to be empty.")
                    return []

                log(f"Reading FIDs from {os.path.basename(file_path)} (Encoding: {encoding}, Format: {format_detected})")

                import io
                file_like = io.StringIO(content)
                reader = csv.reader(file_like)
                processed_count = 0
                ignored_count = 0

                for row_num, row in enumerate(reader, 1):
                    if not row:
                        continue

                    if format_detected == "comma-separated":
                        items_in_row = 0
                        for item in row:
                            fid = item.strip()
                            if fid.isdigit():
                                player_ids.append(fid)
                                items_in_row +=1
                            elif fid:
                                ignored_count += 1
                        if items_in_row > 0 : processed_count += items_in_row
                    else:
                        fid = row[0].strip()
                        if fid.isdigit():
                            player_ids.append(fid)
                            processed_count += 1
                        elif fid:
                            ignored_count += 1

                if ignored_count > 0:
                    log(f"Ignored {ignored_count} non-numeric or empty entries in {os.path.basename(file_path)}.")

                return player_ids

        except FileNotFoundError:
            raise
        except UnicodeDecodeError:
            continue
        except Exception as e:
            log(f"Error reading or processing CSV file {os.path.basename(file_path)} with encoding {encoding}: {str(e)}")
            return []

    log(f"Error: Could not decode file {os.path.basename(file_path)} with tried encodings: {encodings_to_try}")
    return []

def print_summary():
    script_end_time = time.time()
    total_seconds = script_end_time - script_start_time
    execution_time = str(timedelta(seconds=int(total_seconds)))

    log("\n" + "="*25 + " Redemption Summary " + "="*25)
    log(f"Code Redeemed: {args.code}")
    log(f"Total Unique FIDs Found: {len(all_player_ids)}")
    log(f"Total FIDs Processed (Final Status Reached): {len(processed_fids)}")

    remaining_fids = len(all_player_ids) - len(processed_fids)
    if remaining_fids > 0:
        log(f"FIDs Not Processed (in queue or script stopped early): {remaining_fids}")

    log(f"\n--- Redemption Results ---")
    log(f"Successfully redeemed: {counters['success']}")
    log(f"Already redeemed: {counters['already_redeemed']}")
    known_exits = sum(1 for fid, msg in error_details.items() if msg in ["Code has expired", "Claim limit reached, unable to claim"])
    actual_errors = counters['errors'] - known_exits
    log(f"Code Expired / Limit Reached: {known_exits}")
    log(f"Other Errors/Failures: {actual_errors}")

    if error_details:
        log("\n--- Error Details (FID: Last Error) ---")
        sorted_errors = sorted(error_details.items(), key=lambda item: int(item[0]) if item[0].isdigit() else float('inf'))
        errors_to_show = [(fid, msg) for fid, msg in sorted_errors if msg not in ["Code has expired", "Claim limit reached, unable to claim"]]
        if errors_to_show:
            max_errors_to_show = 20
            for i, (fid, msg) in enumerate(errors_to_show):
                if i < max_errors_to_show:
                    log(f"  {fid}: {msg}")
                elif i == max_errors_to_show:
                    log(f"  ... (and {len(errors_to_show) - max_errors_to_show} more errors)")
                    break
        else:
            log("  No errors recorded (excluding expired/limit reached).")

    log("\n" + "="*25 + " Captcha Statistics " + "="*25)
    log(f"OCR Method Used: {args.ocr_method}")
    log(f"Total Captcha Fetches Attempted: {counters['captcha_fetch_attempts']}")
    log(f"Total OCR Recognition Calls: {counters['captcha_ocr_attempts']}")
    log(f"Successful OCR (Valid Format): {counters['captcha_ocr_success']}")
    if args.ocr_method == 'captchacracker':
        log(f"  └─ CaptchaCracker Successes: {counters['captcha_ocr_success_cc']}")
    if args.ocr_method == 'easyocr':
        log(f"  └─ EasyOCR Successes: {counters['captcha_ocr_success_easyocr']}")
    if args.ocr_method == 'ddddocr':
        log(f"  └─ DdddOcr Successes: {counters['captcha_ocr_success_ddddocr']}")

    total_server_validations = counters['captcha_redeem_success'] + counters['captcha_redeem_failure']
    log(f"Total Captcha Submissions (Attempts Sent to Server): {total_server_validations}")
    log(f"  ├─ Passed Server Validation: {counters['captcha_redeem_success']}")
    log(f"  └─ Failed Server Validation: {counters['captcha_redeem_failure']}")
    log(f"Rate Limited Events (Fetch/Redeem): {counters['captcha_rate_limited']}")

    ocr_success_rate = (counters['captcha_ocr_success'] / counters['captcha_ocr_attempts'] * 100) if counters['captcha_ocr_attempts'] > 0 else 0
    server_pass_rate_overall = (counters['captcha_redeem_success'] / total_server_validations * 100) if total_server_validations > 0 else 0

    log(f"\nOCR Success Rate (Valid Format / OCR Calls): {ocr_success_rate:.2f}%")
    log(f"Server Pass Rate (Passed / Total Submissions): {server_pass_rate_overall:.2f}%")
    if args.save_images > 0:
        log(f"Captcha images saved to: {os.path.relpath(CAPTCHA_SAVE_DIR)}")


    log(f"\nTotal execution time: {execution_time}")
    log("="*70)

if __name__ == "__main__":
    start_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log(f"\n=== Starting redemption script at {start_time_str} ===")
    log(f"Gift Code: {args.code}")
    log(f"Selected OCR Method: {args.ocr_method}")
    if args.ocr_method == 'easyocr' and args.use_gpu is not None: log(f"  (EasyOCR GPU Preference: Device {args.use_gpu})")
    elif args.ocr_method == 'captchacracker' and args.use_gpu is not None: log("  (CaptchaCracker GPU Hint: TensorFlow/ONNX usually auto-detects)")
    elif args.ocr_method == 'ddddocr' and args.use_gpu is not None: log("  (DdddOcr GPU Hint: ONNX Runtime usually auto-detects)")
    log(f"Save Images Mode: {args.save_images} (0:None, 1:Failed, 2:Success, 3:All)")

    # Handle CSV input path/pattern
    csv_input_spec = args.csv or "."
    csv_files = []
    if os.path.isdir(csv_input_spec):
        csv_pattern = os.path.join(csv_input_spec, "*.csv")
        csv_files = sorted(glob(csv_pattern))
        log(f"Searching for CSV files in directory: {csv_input_spec} (Pattern: *.csv)")
    elif "*" in os.path.basename(csv_input_spec) or "?" in os.path.basename(csv_input_spec):
        csv_pattern = csv_input_spec
        csv_files = sorted(glob(csv_pattern))
        log(f"Searching for CSV files matching pattern: {csv_pattern}")
    elif os.path.isfile(csv_input_spec) and csv_input_spec.lower().endswith(".csv"):
        csv_files = [csv_input_spec]
        log(f"Using specified CSV file: {csv_input_spec}")
    else:
        log(f"Warning: Input '{csv_input_spec}' is not a directory, CSV file, or valid pattern. Checking current directory for *.csv.")
        csv_pattern = os.path.join(".", "*.csv")
        csv_files = sorted(glob(csv_pattern))


    if not csv_files:
        log(f"Error: No CSV files found matching the search criteria. Please specify a valid CSV file, directory, or pattern using --csv.")
        sys.exit(1)
    else:
        log(f"Found {len(csv_files)} CSV file(s) to process: {', '.join(os.path.basename(f) for f in csv_files)}")

    # --- Load All Player IDs ---
    retry_queue = {}
    all_player_ids_raw = []
    for csv_file in csv_files:
        try:
            player_ids = read_player_ids_from_csv(csv_file)
            if player_ids:
                log(f"Loaded {len(player_ids)} potential player IDs from {os.path.basename(csv_file)}")
                all_player_ids_raw.extend(player_ids)
        except FileNotFoundError:
            log(f"Error: CSV file '{os.path.basename(csv_file)}' not found during loading loop.")
        except Exception as e:
            log(f"Error processing {os.path.basename(csv_file)} during loading loop: {str(e)}")

    # Remove duplicates and ensure list contains only unique valid digit strings
    original_count = len(all_player_ids_raw)
    all_player_ids_set = {fid for fid in all_player_ids_raw if fid.isdigit()}
    all_player_ids = sorted(list(all_player_ids_set), key=int)
    duplicates_removed = original_count - len(all_player_ids)

    if duplicates_removed > 0:
        log(f"Removed {duplicates_removed} duplicate or invalid entries.")

    if not all_player_ids:
        log("Error: No valid, unique, numeric player IDs loaded from any CSV file. Exiting.")
        sys.exit(1)

    log(f"Total unique valid player IDs to process: {len(all_player_ids)}")

    # --- Redemption Loop ---
    processed_fids = set()
    stop_processing = False

    # Main loop continues as long as there are FIDs not processed and no stop signal
    while len(processed_fids) < len(all_player_ids) and not stop_processing:
        current_time = time.time()
        processed_in_this_cycle = 0
        fids_processed_in_cycle = []

        fids_to_process_now = []
        fids_in_cooldown_count = 0

        # Determine which FIDs to process now vs wait
        for fid in all_player_ids:
            if fid in processed_fids: continue
            if fid in retry_queue and retry_queue[fid] > current_time:
                fids_in_cooldown_count += 1
            else:
                fids_to_process_now.append(fid)

        if not fids_to_process_now and fids_in_cooldown_count > 0:
            next_retry_time = min(retry_queue[fid] for fid in all_player_ids if fid not in processed_fids and fid in retry_queue)
            wait_time = max(1, min(30, next_retry_time - current_time + 1)) # Wait at least 1s, max 30s or until next retry + 1s
            log(f"{fids_in_cooldown_count} FIDs in cooldown. Waiting {int(wait_time)}s... Progress: {len(processed_fids)}/{len(all_player_ids)}")
            time.sleep(wait_time)
            continue

        if not fids_to_process_now and fids_in_cooldown_count == 0:
            log("Warning: No FIDs to process now and none in cooldown, but not all FIDs are processed. Check logic.")
            break

        # Process FIDs that are ready in this cycle
        log(f"\n--- Starting processing cycle. Ready FIDs: {len(fids_to_process_now)} ---")
        for fid in fids_to_process_now:
            if fid in processed_fids: continue
            if stop_processing: break

            result, retry_queue = redeem_gift_code(fid, args.code, retry_queue)
            processed_in_this_cycle += 1
            fids_processed_in_cycle.append(fid)

            raw_msg = result.get('msg', 'Unknown error').strip('.')
            friendly_msg = RESULT_MESSAGES.get(raw_msg, raw_msg)
            is_queued_for_retry = fid in retry_queue and retry_queue[fid] > time.time()

            is_final_state = not is_queued_for_retry
            if is_final_state:
                processed_fids.add(fid)
                if raw_msg in ["SUCCESS", "SAME TYPE EXCHANGE"]:
                    counters["success"] += 1
                elif raw_msg == "RECEIVED":
                    counters["already_redeemed"] += 1
                elif raw_msg not in ["TIME ERROR", "USED", "Invalid FID format", "In cooldown", "CAPTCHA CHECK TOO FREQUENT"]:
                    counters["errors"] += 1
                    friendly_msg_for_error = RESULT_MESSAGES.get(raw_msg, raw_msg)
                    error_details[fid] = friendly_msg_for_error

            if raw_msg == "TIME ERROR":
                log("\n *** Code has expired! Stopping further processing. ***")
                stop_processing = True
            elif raw_msg == "USED":
                log("\n *** Claim limit reached! Stopping further processing. ***")
                stop_processing = True

            if not stop_processing:
                time.sleep(DELAY + random.uniform(0, 0.5))

        log(f"--- Processing cycle finished. Processed {processed_in_this_cycle} FIDs in this cycle. Total processed: {len(processed_fids)}/{len(all_player_ids)} ---")

    if stop_processing:
        log("\nProcessing stopped due to code expiration or claim limit.")
    elif len(processed_fids) == len(all_player_ids):
        log(f"\nAll {len(processed_fids)} FIDs processed.")
    else:
        log(f"\nProcessing loop finished. Processed {len(processed_fids)} out of {len(all_player_ids)} FIDs.")

    print_summary()
    sys.exit(0)