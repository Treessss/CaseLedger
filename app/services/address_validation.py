"""收件地址真实性/一致性校验（4PX 预申报前）。

没有接付费地址库时，用 GeoNames 邮编库 + 国家邮编格式 + 邮编/州一致性 + 占位信息识别
做第一道真实性校验：明显假地址、邮编与州不匹配、占位电话/地址会拦下，进入「预申报人工处理」页。
GeoNames 数据为 CC BY 4.0，生成脚本与日期见 app/data/postal_regions.json 的 source 字段。
"""

import json
import re
from functools import lru_cache
from pathlib import Path

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
    "PR": "Puerto Rico", "VI": "Virgin Islands", "AE": "Armed Forces Americas",
    "AP": "Armed Forces Pacific", "AA": "Armed Forces Europe",
}

AU_STATES = {
    "NSW": "New South Wales", "VIC": "Victoria", "QLD": "Queensland", "SA": "South Australia",
    "WA": "Western Australia", "TAS": "Tasmania", "NT": "Northern Territory",
    "ACT": "Australian Capital Territory",
}

CA_PROVINCE_NAMES = {
    "NL": "Newfoundland and Labrador", "NS": "Nova Scotia", "PE": "Prince Edward Island",
    "NB": "New Brunswick", "QC": "Quebec", "ON": "Ontario", "MB": "Manitoba",
    "SK": "Saskatchewan", "AB": "Alberta", "BC": "British Columbia",
    "NT": "Northwest Territories", "YT": "Yukon", "NU": "Nunavut",
}

GB_POSTCODE_RE = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}$")
GB_AREAS = {
    "AB", "AL", "B", "BA", "BB", "BD", "BH", "BL", "BN", "BR", "BS", "BT", "CA", "CB", "CF",
    "CH", "CM", "CO", "CR", "CT", "CV", "CW", "DA", "DD", "DE", "DG", "DH", "DL", "DN", "DT",
    "DY", "E", "EC", "EH", "EN", "EX", "FK", "FY", "G", "GL", "GU", "HA", "HD", "HG", "HP",
    "HR", "HS", "HU", "HX", "IG", "IP", "IV", "KA", "KT", "KW", "KY", "L", "LA", "LD", "LE",
    "LL", "LN", "LS", "LU", "LW", "M", "ME", "MK", "ML", "N", "NE", "NG", "NN", "NP", "NR",
    "NW", "OL", "OX", "PA", "PE", "PH", "PL", "PO", "PR", "RG", "RH", "RM", "S", "SA", "SE",
    "SG", "SK", "SL", "SM", "SN", "SO", "SP", "SR", "SS", "ST", "SW", "SY", "TA", "TD", "TF",
    "TN", "TQ", "TR", "TS", "TW", "UB", "W", "WA", "WC", "WD", "WF", "WN", "WR", "WS", "WV",
    "YO", "ZE",
}

SUSPICIOUS_RE = re.compile(r"\b(test|tests|xxx|x{3,}|n/?a|unknown|fake|asdf|qwer|dummy)\b", re.I)
PLACEHOLDER_PHONES = {"0000000000", "1111111111", "1234567890", "0123456789", "9999999999"}
US_TERRITORY_PREFIXES = {"006", "007", "008", "009"}
# 部分真实 ZIP 未被 GeoNames 收录（例如 32846）时，用 3 位前缀兜底判断州是否一致。
US_ZIP_PREFIX_RANGES = (
    (5, 5, "NY"), (6, 9, "PR"), (10, 27, "MA"), (28, 29, "RI"), (30, 38, "NH"),
    (39, 49, "ME"), (50, 59, "VT"), (60, 69, "CT"), (70, 89, "NJ"), (100, 149, "NY"),
    (150, 196, "PA"), (197, 199, "DE"), (200, 200, "DC"), (201, 201, "VA"),
    (202, 205, "DC"), (206, 219, "MD"), (220, 246, "VA"), (247, 268, "WV"),
    (270, 289, "NC"), (290, 299, "SC"), (300, 319, "GA"), (320, 349, "FL"),
    (350, 369, "AL"), (370, 385, "TN"), (386, 397, "MS"), (398, 399, "GA"),
    (400, 427, "KY"), (430, 459, "OH"), (460, 479, "IN"), (480, 499, "MI"),
    (500, 528, "IA"), (530, 549, "WI"), (550, 567, "MN"), (570, 577, "SD"),
    (580, 588, "ND"), (590, 599, "MT"), (600, 629, "IL"), (630, 658, "MO"),
    (660, 679, "KS"), (680, 693, "NE"), (700, 714, "LA"), (716, 729, "AR"),
    (730, 749, "OK"), (750, 799, "TX"), (800, 816, "CO"), (820, 831, "WY"),
    (832, 838, "ID"), (840, 847, "UT"), (850, 865, "AZ"), (870, 884, "NM"),
    (885, 885, "TX"), (889, 898, "NV"), (900, 961, "CA"), (967, 968, "HI"),
    (970, 979, "OR"), (980, 994, "WA"), (995, 999, "AK"),
)
US_APO_PREFIXES = {"340", "090", "091", "092", "093", "094", "095", "096", "097", "098",
                   "962", "963", "964", "965", "966"}
CA_LETTER_PROVINCES = {
    "A": {"NL"}, "B": {"NS"}, "C": {"PE"}, "E": {"NB"}, "G": {"QC"}, "H": {"QC"},
    "J": {"QC"}, "K": {"ON"}, "L": {"ON"}, "M": {"ON"}, "N": {"ON"}, "P": {"ON"},
    "R": {"MB"}, "S": {"SK"}, "T": {"AB"}, "V": {"BC"}, "X": {"NT", "NU"}, "Y": {"YT"},
}


@lru_cache(maxsize=1)
def _postal_regions():
    path = Path(__file__).resolve().parent.parent / "data" / "postal_regions.json"
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _norm(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _digits(value):
    return re.sub(r"[^0-9]", "", str(value or ""))


def _state_code(country, address):
    raw = _norm(address.get("province_code") or address.get("province"))
    if not raw:
        return None
    value = raw.upper()
    if country == "US":
        if value in US_STATES:
            return value
        for code, name in US_STATES.items():
            if value == name.upper():
                return code
    elif country == "AU":
        if value in AU_STATES:
            return value
        for code, name in AU_STATES.items():
            if value == name.upper():
                return code
    elif country == "CA":
        if value in CA_PROVINCE_NAMES:
            return value
        for code, name in CA_PROVINCE_NAMES.items():
            if value == name.upper():
                return code
    return None


def _allowed_states(value):
    if isinstance(value, list):
        return set(value)
    return {value} if value else set()


def _us_state_for_prefix(zip5):
    try:
        prefix = int(zip5[:3])
    except (TypeError, ValueError):
        return None
    for start, end, code in US_ZIP_PREFIX_RANGES:
        if start <= prefix <= end:
            return code
    return None


def _suspicious(address, phone):
    issues = []
    street = _norm(f"{address.get('address1') or ''} {address.get('address2') or ''}")
    name = _norm(address.get("name") or f"{address.get('first_name') or ''} {address.get('last_name') or ''}")
    if not name:
        issues.append("缺少收件人姓名，请核对")
    digits = _digits(phone)
    if digits and (len(digits) < 6 or len(set(digits)) == 1 or digits in PLACEHOLDER_PHONES):
        issues.append("收货电话疑似占位/无效，请核对")
    if SUSPICIOUS_RE.search(f"{street} {address.get('city') or ''} {name}"):
        issues.append("收货地址疑似测试/占位信息，请核对")
    return issues


def validate_recipient(order):
    """返回地址/联系方式问题列表；空列表表示通过本地真实性校验。"""
    address = dict(order.shipping_address or {})
    country = _norm(address.get("country_code") or "").upper()
    phone = _norm(address.get("phone") or getattr(order, "customer_phone", "") or "")
    postal = _norm(address.get("zip")).upper()
    issues = []
    regions = _postal_regions()

    if country == "US":
        zip5 = postal.split("-")[0]
        if not re.fullmatch(r"[0-9]{5}", zip5):
            issues.append("美国邮编格式不正确（应为 5 位或 ZIP+4），请核对")
        else:
            expected = _allowed_states(regions.get("US", {}).get(zip5))
            actual = _state_code("US", address)
            if not expected:
                if zip5[:3] in US_TERRITORY_PREFIXES or zip5[:3] in US_APO_PREFIXES:
                    expected = set()
                else:
                    fallback = _us_state_for_prefix(zip5)
                    if fallback:
                        expected = {fallback}
                    elif actual:
                        issues.append("美国邮编不在邮编库中，可能不是真实地址，请核对")
            if expected and actual and actual not in expected:
                issues.append(f"美国邮编与州不一致（邮编对应 {'/'.join(sorted(expected))}，订单填 {actual}），请核对")
    elif country == "AU":
        if not re.fullmatch(r"[0-9]{4}", postal):
            issues.append("澳大利亚邮编格式不正确（应为 4 位数字），请核对")
        else:
            expected = _allowed_states(regions.get("AU", {}).get(postal))
            actual = _state_code("AU", address)
            if expected and actual and actual not in expected:
                issues.append(f"澳大利亚邮编与州不一致（邮编对应 {'/'.join(sorted(expected))}，订单填 {actual}），请核对")
            elif not expected:
                issues.append("澳大利亚邮编不在邮编库中，可能不是真实地址，请核对")
    elif country == "CA":
        compact = postal.replace(" ", "")
        if not re.fullmatch(r"[ABCEGHJ-NPRSTVXY][0-9][A-Z][0-9][A-Z][0-9]", compact):
            issues.append("加拿大邮编格式不正确（应为 A1A 1A1），请核对")
        else:
            expected = CA_LETTER_PROVINCES.get(compact[0], set())
            actual = _state_code("CA", address)
            if expected and actual and actual not in expected:
                issues.append(f"加拿大邮编与省不一致（邮编首字母对应 {'/'.join(sorted(expected))}，订单填 {actual}），请核对")
    elif country == "GB":
        if not GB_POSTCODE_RE.fullmatch(postal):
            issues.append("英国邮编格式不正确，请核对")
        else:
            area = re.match(r"[A-Z]{1,2}", postal).group(0)
            if area not in GB_AREAS:
                issues.append("英国邮编区号不存在，请核对")
    elif country == "NZ":
        if not re.fullmatch(r"[0-9]{4}", postal):
            issues.append("新西兰邮编格式不正确（应为 4 位数字），请核对")
    elif country == "NL":
        if not re.fullmatch(r"[0-9]{4} ?[A-Z]{2}", postal):
            issues.append("荷兰邮编格式不正确（应为 1234 AB），请核对")

    issues.extend(_suspicious(address, phone))
    return issues
