from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import requests
import urllib.parse

def correct_address(address):
    # 예외 주소 사전
    correction_map = {
        "한누리대로 2130": "세종특별자치시 한누리대로 2130",
        "세종시청": "세종특별자치시 보람동 한누리대로 2130",
        "보람동 218": "세종특별자치시 보람동 218",
        "속초시청": "강원도 속초시 중앙동 중앙로 183",
        "중앙동 469-6": "강원도 속초시 중앙동 469-6"
    }

    if address in correction_map:
        return correction_map[address]

    # 세종시 자동 보정
    if "세종" in address and "세종특별자치시" not in address:
        return "세종특별자치시 " + address
    # 속초시 자동 보정
    if "속초" in address and "강원도" not in address:
        return "강원도 " + address

    return address

def geocode_address(address):
    base_url = "https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": "vsdzf1f4n5",
        "X-NCP-APIGW-API-KEY": "0gzctO51PUTVv0gUZU025JYNHPTmVzLS9sGbfYBM"
    }

    def request_geocode(addr):
        query = urllib.parse.quote(addr)
        url = f"{base_url}?query={query}"
        response = requests.get(url, headers=headers)
        try:
            result = response.json()
            if result.get("addresses"):
                lat = float(result["addresses"][0]["y"])
                lon = float(result["addresses"][0]["x"])
                return lat, lon, addr
        except Exception as e:
            print(f"API 응답 오류: {e}")
        return None

    # 1차 시도
    result = request_geocode(address)
    if result:
        return result

    # 2차: 자동 보정 주소로 재시도
    corrected = correct_address(address)
    if corrected != address:
        result = request_geocode(corrected)
        if result:
            return result

    # 3차: 실패 응답
    return None

# 예시 테스트
if __name__ == "__main__":
    test_addresses = [
        "한누리대로 2130",
        "세종시청",
        "보람동 218",
        "속초시청",
        "중앙동 469-6"
    ]
    for addr in test_addresses:
        print(f"\n[테스트 주소] {addr}")
        result = geocode_address(addr)
        if result:
            print(f"  ↳ 성공: 위도={result[0]}, 경도={result[1]}, 주소={result[2]}")
        else:
            print("  ↳ 실패: 주소를 찾을 수 없습니다.")
