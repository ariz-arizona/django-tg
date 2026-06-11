class Se:
    @staticmethod
    def vol_host_v2(e):
        s = e // 100000
        # Полное соответствие логике JS
        if 0 <= s <= 143: r = "01"
        elif s <= 287: r = "02"
        elif s <= 431: r = "03"
        elif s <= 719: r = "04"
        elif s <= 1007: r = "05"
        elif s <= 1061: r = "06"
        elif s <= 1115: r = "07"
        elif s <= 1169: r = "08"
        elif s <= 1313: r = "09"
        elif s <= 1601: r = "10"
        elif s <= 1655: r = "11"
        elif s <= 1919: r = "12"
        elif s <= 2045: r = "13"
        elif s <= 2189: r = "14"
        elif s <= 2405: r = "15"
        elif s <= 2621: r = "16"
        elif s <= 2837: r = "17"
        elif s <= 3053: r = "18"
        elif s <= 3269: r = "19"
        elif s <= 3485: r = "20"
        elif s <= 3701: r = "21"
        elif s <= 3917: r = "22"
        elif s <= 4133: r = "23"
        elif s <= 4349: r = "24"
        elif s <= 4565: r = "25"
        elif s <= 4877: r = "26"
        elif s <= 5189: r = "27"
        elif s <= 5501: r = "28"
        elif s <= 5813: r = "29"
        elif s <= 6125: r = "30"
        elif s <= 6437: r = "31"
        elif s <= 6749: r = "32"
        elif s <= 7061: r = "33"
        elif s <= 7373: r = "34"
        elif s <= 7685: r = "35"
        elif s <= 7997: r = "36"
        elif s <= 8309: r = "37"
        elif s <= 8741: r = "38"
        elif s <= 9173: r = "39"
        elif s <= 9605: r = "40"
        elif s <= 10373: r = "41"
        elif s <= 11141: r = "42"
        elif s <= 11909: r = "43"
        elif s <= 12677: r = "44"
        elif s <= 13445: r = "45"
        elif s <= 14213: r = "46"
        else: r = "47"
        return f"basket-{r}.wbbasket.ru/vol{s}"

    @staticmethod
    def vol_video_host(e):
        s = e % 144
        if 0 <= s <= 11: r = "01"
        elif s <= 23: r = "02"
        elif s <= 35: r = "03"
        elif s <= 47: r = "04"
        elif s <= 59: r = "05"
        elif s <= 71: r = "06"
        elif s <= 83: r = "07"
        elif s <= 95: r = "08"
        elif s <= 107: r = "09"
        elif s <= 119: r = "10"
        elif s <= 131: r = "11"
        elif s <= 143: r = "12"
        else: r = "13"
        return f"videonme-basket-{r}.wbbasket.ru/vol{s}"

    @staticmethod
    def construct_host_v2(e, t="nm"):
        s = int(e)
        i = (s // 10000) if t == "video" else (s // 1000)
        
        if t == "nm":
            host = Se.vol_host_v2(s)
        elif t == "video":
            host = Se.vol_video_host(s)
        else:
            host = "" # Здесь можно добавить логику для feedbackPhoto, если нужно
            
        return f"https://{host}/part{i}/{s}"