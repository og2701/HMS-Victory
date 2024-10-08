
from lib.utils import load_whitelist
from collections import defaultdict

class ROLES:
	DEPUTY_PM = 960538130761527386
	MINISTER = 1250190944502943755
	CABINET = 959493505930121226
	BORDER_FORCE = 959500686746345542
	SERVER_BOOSTER = 959650957325635707
	POLITICS_BAN = 1265295557115510868
	BALL_INSPECTOR = 1197712388493934692

class CHANNELS:
	COMMONS = 959501347571531776
	BOT_SPAM = 968502541107228734
	POLITICS = 1141097424849481799
	PORT_OF_DOVER = 1131633452022767698
	MEMBER_UPDATES = 1279873633602244668 #on secret server
	DATA_BACKUP = 1281734214756335757 #on secret server
	IMAGE_CACHE = 1271188365244497971 #on secret server

class USERS:
	OGGERS = 404634271861571584
	COUNTRYBALL_BOT = 999736048596816014

POLITICS_WHITELISTED_USER_IDS = load_whitelist()

command_usage_tracker = defaultdict(lambda: {'count': 0, 'last_used': None})

SUMMARISE_DAILY_LIMIT = 10

FLAG_LANGUAGE_MAPPINGS = {
    ":flag_ac:": "English",  # Ascension Island
    ":flag_ad:": "Catalan",  # Andorra
    ":flag_ae:": "Arabic",  # United Arab Emirates
    ":flag_af:": "Pashto",  # Afghanistan
    ":flag_ag:": "English",  # Antigua and Barbuda
    ":flag_ai:": "English",  # Anguilla
    ":flag_al:": "Albanian",  # Albania
    ":flag_am:": "Armenian",  # Armenia
    ":flag_ao:": "Portuguese",  # Angola
    ":flag_ar:": "Spanish",  # Argentina
    ":flag_as:": "Samoan",  # American Samoa
    ":flag_at:": "German",  # Austria
    ":flag_au:": "English",  # Australia
    ":flag_aw:": "Papiamento",  # Aruba
    ":flag_ax:": "Swedish",  # Åland Islands
    ":flag_az:": "Azerbaijani",  # Azerbaijan
    ":flag_ba:": "Bosnian",  # Bosnia and Herzegovina
    ":flag_bb:": "English",  # Barbados
    ":flag_bd:": "Bengali",  # Bangladesh
    ":flag_be:": "Dutch",  # Belgium
    ":flag_bf:": "French",  # Burkina Faso
    ":flag_bg:": "Bulgarian",  # Bulgaria
    ":flag_bh:": "Arabic",  # Bahrain
    ":flag_bi:": "Kirundi",  # Burundi
    ":flag_bj:": "French",  # Benin
    ":flag_bl:": "French",  # Saint Barthélemy
    ":flag_bm:": "English",  # Bermuda
    ":flag_bn:": "Malay",  # Brunei
    ":flag_bo:": "Spanish",  # Bolivia
    ":flag_bq:": "Dutch",  # Caribbean Netherlands
    ":flag_br:": "Portuguese",  # Brazil
    ":flag_bs:": "English",  # Bahamas
    ":flag_bt:": "Dzongkha",  # Bhutan
    ":flag_bv:": "Norwegian",  # Bouvet Island
    ":flag_bw:": "English",  # Botswana
    ":flag_by:": "Belarusian",  # Belarus
    ":flag_bz:": "English",  # Belize
    ":flag_ca:": "English",  # Canada
    ":flag_cc:": "English",  # Cocos (Keeling) Islands
    ":flag_cd:": "French",  # Democratic Republic of the Congo
    ":flag_cf:": "French",  # Central African Republic
    ":flag_cg:": "French",  # Republic of the Congo
    ":flag_ch:": "German",  # Switzerland
    ":flag_ci:": "French",  # Côte d'Ivoire
    ":flag_ck:": "English",  # Cook Islands
    ":flag_cl:": "Spanish",  # Chile
    ":flag_cm:": "French",  # Cameroon
    ":flag_cn:": "Mandarin Chinese",  # China
    ":flag_co:": "Spanish",  # Colombia
    ":flag_cp:": "English",  # Clipperton Island
    ":flag_cr:": "Spanish",  # Costa Rica
    ":flag_cu:": "Spanish",  # Cuba
    ":flag_cv:": "Portuguese",  # Cape Verde
    ":flag_cw:": "Papiamento",  # Curaçao
    ":flag_cx:": "English",  # Christmas Island
    ":flag_cy:": "Greek",  # Cyprus
    ":flag_cz:": "Czech",  # Czech Republic
    ":flag_de:": "German",  # Germany
    ":flag_dg:": "English",  # Diego Garcia
    ":flag_dj:": "French",  # Djibouti
    ":flag_dk:": "Danish",  # Denmark
    ":flag_dm:": "English",  # Dominica
    ":flag_do:": "Spanish",  # Dominican Republic
    ":flag_dz:": "Arabic",  # Algeria
    ":flag_ec:": "Spanish",  # Ecuador
    ":flag_ee:": "Estonian",  # Estonia
    ":flag_eg:": "Arabic",  # Egypt
    ":flag_er:": "Tigrinya",  # Eritrea
    ":flag_es:": "Spanish",  # Spain
    ":flag_et:": "Amharic",  # Ethiopia
    ":flag_fi:": "Finnish",  # Finland
    ":flag_fj:": "English",  # Fiji
    ":flag_fk:": "English",  # Falkland Islands
    ":flag_fm:": "English",  # Micronesia
    ":flag_fo:": "Faroese",  # Faroe Islands
    ":flag_fr:": "French",  # France
    ":flag_ga:": "French",  # Gabon
    ":flag_gb:": "English",  # United Kingdom
    ":flag_gd:": "English",  # Grenada
    ":flag_ge:": "Georgian",  # Georgia
    ":flag_gf:": "French",  # French Guiana
    ":flag_gg:": "English",  # Guernsey
    ":flag_gh:": "English",  # Ghana
    ":flag_gi:": "English",  # Gibraltar
    ":flag_gl:": "Greenlandic",  # Greenland
    ":flag_gm:": "English",  # Gambia
    ":flag_gn:": "French",  # Guinea
    ":flag_gp:": "French",  # Guadeloupe
    ":flag_gq:": "Spanish",  # Equatorial Guinea
    ":flag_gr:": "Greek",  # Greece
    ":flag_gt:": "Spanish",  # Guatemala
    ":flag_gu:": "English",  # Guam
    ":flag_gw:": "Portuguese",  # Guinea-Bissau
    ":flag_gy:": "English",  # Guyana
    ":flag_hk:": "Chinese",  # Hong Kong
    ":flag_hn:": "Spanish",  # Honduras
    ":flag_hr:": "Croatian",  # Croatia
    ":flag_ht:": "Haitian Creole",  # Haiti
    ":flag_hu:": "Hungarian",  # Hungary
    ":flag_id:": "Indonesian",  # Indonesia
    ":flag_ie:": "English",  # Ireland
    ":flag_il:": "Hebrew",  # Israel
    ":flag_im:": "English",  # Isle of Man
    ":flag_in:": "Hindi",  # India
    ":flag_iq:": "Arabic",  # Iraq
    ":flag_ir:": "Persian",  # Iran
    ":flag_is:": "Icelandic",  # Iceland
    ":flag_it:": "Italian",  # Italy
    ":flag_je:": "English",  # Jersey
    ":flag_jm:": "English",  # Jamaica
    ":flag_jo:": "Arabic",  # Jordan
    ":flag_jp:": "Japanese",  # Japan
    ":flag_ke:": "Swahili",  # Kenya
    ":flag_kg:": "Kyrgyz",  # Kyrgyzstan
    ":flag_kh:": "Khmer",  # Cambodia
    ":flag_ki:": "English",  # Kiribati
    ":flag_km:": "Comorian",  # Comoros
    ":flag_kn:": "English",  # Saint Kitts and Nevis
    ":flag_kp:": "Korean",  # North Korea
    ":flag_kr:": "Korean",  # South Korea
    ":flag_kw:": "Arabic",  # Kuwait
    ":flag_ky:": "English",  # Cayman Islands
    ":flag_kz:": "Kazakh",  # Kazakhstan
    ":flag_la:": "Lao",  # Laos
    ":flag_lb:": "Arabic",  # Lebanon
    ":flag_lc:": "English",  # Saint Lucia
    ":flag_li:": "German",  # Liechtenstein
    ":flag_lk:": "Sinhala",  # Sri Lanka
    ":flag_lr:": "English",  # Liberia
    ":flag_ls:": "Sesotho",  # Lesotho
    ":flag_lt:": "Lithuanian",  # Lithuania
    ":flag_lu:": "Luxembourgish",  # Luxembourg
    ":flag_lv:": "Latvian",  # Latvia
    ":flag_ly:": "Arabic",  # Libya
    ":flag_ma:": "Arabic",  # Morocco
    ":flag_mc:": "French",  # Monaco
    ":flag_md:": "Romanian",  # Moldova
    ":flag_me:": "Montenegrin",  # Montenegro
    ":flag_mf:": "French",  # Saint Martin
    ":flag_mg:": "Malagasy",  # Madagascar
    ":flag_mh:": "Marshallese",  # Marshall Islands
    ":flag_mk:": "Macedonian",  # North Macedonia
    ":flag_ml:": "French",  # Mali
    ":flag_mm:": "Burmese",  # Myanmar
    ":flag_mn:": "Mongolian",  # Mongolia
    ":flag_mo:": "Chinese",  # Macau
    ":flag_mp:": "English",  # Northern Mariana Islands
    ":flag_mq:": "French",  # Martinique
    ":flag_mr:": "Arabic",  # Mauritania
    ":flag_ms:": "English",  # Montserrat
    ":flag_mt:": "Maltese",  # Malta
    ":flag_mu:": "English",  # Mauritius
    ":flag_mv:": "Dhivehi",  # Maldives
    ":flag_mw:": "English",  # Malawi
    ":flag_mx:": "Spanish",  # Mexico
    ":flag_my:": "Malay",  # Malaysia
    ":flag_mz:": "Portuguese",  # Mozambique
    ":flag_na:": "English",  # Namibia
    ":flag_nc:": "French",  # New Caledonia
    ":flag_ne:": "French",  # Niger
    ":flag_nf:": "English",  # Norfolk Island
    ":flag_ng:": "English",  # Nigeria
    ":flag_ni:": "Spanish",  # Nicaragua
    ":flag_nl:": "Dutch",  # Netherlands
    ":flag_no:": "Norwegian",  # Norway
    ":flag_np:": "Nepali",  # Nepal
    ":flag_nr:": "Nauruan",  # Nauru
    ":flag_nu:": "English",  # Niue
    ":flag_nz:": "English",  # New Zealand
    ":flag_om:": "Arabic",  # Oman
    ":flag_pa:": "Spanish",  # Panama
    ":flag_pe:": "Spanish",  # Peru
    ":flag_pf:": "French",  # French Polynesia
    ":flag_pg:": "English",  # Papua New Guinea
    ":flag_ph:": "Filipino",  # Philippines
    ":flag_pk:": "Urdu",  # Pakistan
    ":flag_pl:": "Polish",  # Poland
    ":flag_pm:": "French",  # Saint Pierre and Miquelon
    ":flag_pn:": "English",  # Pitcairn Islands
    ":flag_pr:": "Spanish",  # Puerto Rico
    ":flag_ps:": "Arabic",  # Palestine
    ":flag_pt:": "Portuguese",  # Portugal
    ":flag_pw:": "Palauan",  # Palau
    ":flag_py:": "Spanish",  # Paraguay
    ":flag_qa:": "Arabic",  # Qatar
    ":flag_re:": "French",  # Réunion
    ":flag_ro:": "Romanian",  # Romania
    ":flag_rs:": "Serbian",  # Serbia
    ":flag_ru:": "Russian",  # Russia
    ":flag_rw:": "Kinyarwanda",  # Rwanda
    ":flag_sa:": "Arabic",  # Saudi Arabia
    ":flag_sb:": "English",  # Solomon Islands
    ":flag_sc:": "Seselwa",  # Seychelles
    ":flag_sd:": "Arabic",  # Sudan
    ":flag_se:": "Swedish",  # Sweden
    ":flag_sg:": "English",  # Singapore
    ":flag_sh:": "English",  # Saint Helena
    ":flag_si:": "Slovene",  # Slovenia
    ":flag_sj:": "Norwegian",  # Svalbard and Jan Mayen
    ":flag_sk:": "Slovak",  # Slovakia
    ":flag_sl:": "English",  # Sierra Leone
    ":flag_sm:": "Italian",  # San Marino
    ":flag_sn:": "French",  # Senegal
    ":flag_so:": "Somali",  # Somalia
    ":flag_sr:": "Dutch",  # Suriname
    ":flag_ss:": "English",  # South Sudan
    ":flag_st:": "Portuguese",  # São Tomé and Príncipe
    ":flag_sv:": "Spanish",  # El Salvador
    ":flag_sx:": "Dutch",  # Sint Maarten
    ":flag_sy:": "Arabic",  # Syria
    ":flag_sz:": "Swazi",  # Eswatini
    ":flag_ta:": "English",  # Tristan da Cunha
    ":flag_tc:": "English",  # Turks and Caicos Islands
    ":flag_td:": "French",  # Chad
    ":flag_tf:": "French",  # French Southern Territories
    ":flag_tg:": "French",  # Togo
    ":flag_th:": "Thai",  # Thailand
    ":flag_tj:": "Tajik",  # Tajikistan
    ":flag_tk:": "Tokelauan",  # Tokelau
    ":flag_tl:": "Tetum",  # Timor-Leste
    ":flag_tm:": "Turkmen",  # Turkmenistan
    ":flag_tn:": "Arabic",  # Tunisia
    ":flag_to:": "Tongan",  # Tonga
    ":flag_tr:": "Turkish",  # Turkey
    ":flag_tt:": "English",  # Trinidad and Tobago
    ":flag_tv:": "Tuvaluan",  # Tuvalu
    ":flag_tw:": "Mandarin Chinese",  # Taiwan
    ":flag_tz:": "Swahili",  # Tanzania
    ":flag_ua:": "Ukrainian",  # Ukraine
    ":flag_ug:": "Swahili",  # Uganda
    ":flag_um:": "English",  # U.S. Minor Outlying Islands
    ":flag_us:": "English",  # United States
    ":flag_uy:": "Spanish",  # Uruguay
    ":flag_uz:": "Uzbek",  # Uzbekistan
    ":flag_va:": "Italian",  # Vatican City
    ":flag_vc:": "English",  # Saint Vincent and the Grenadines
    ":flag_ve:": "Spanish",  # Venezuela
    ":flag_vg:": "English",  # British Virgin Islands
    ":flag_vi:": "English",  # U.S. Virgin Islands
    ":flag_vn:": "Vietnamese",  # Vietnam
    ":flag_vu:": "Bislama",  # Vanuatu
    ":flag_wf:": "French",  # Wallis and Futuna
    ":flag_ws:": "Samoan",  # Samoa
    ":flag_xk:": "Albanian",  # Kosovo
    ":flag_ye:": "Arabic",  # Yemen
    ":flag_yt:": "French",  # Mayotte
    ":flag_za:": "Zulu",  # South Africa
    ":flag_zm:": "English",  # Zambia
    ":flag_zw:": "Shona",  # Zimbabwe
    ":pirate_flag:": "Pirate Speak",  # Pirate Flag (novelty)
}
