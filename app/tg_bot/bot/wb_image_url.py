class Se:
    @staticmethod
    def vol_host_v2(e):
        t = e // 100000
        if 0 <= t <= 143:
            r = "01"
        elif t <= 287:
            r = "02"
        elif t <= 431:
            r = "03"
        elif t <= 719:
            r = "04"
        elif t <= 1007:
            r = "05"
        elif t <= 1061:
            r = "06"
        elif t <= 1115:
            r = "07"
        elif t <= 1169:
            r = "08"
        elif t <= 1313:
            r = "09"
        elif t <= 1601:
            r = "10"
        elif t <= 1655:
            r = "11"
        elif t <= 1919:
            r = "12"
        elif t <= 2045:
            r = "13"
        elif t <= 2189:
            r = "14"
        elif t <= 2405:
            r = "15"
        elif t <= 2621:
            r = "16"
        elif t <= 2837:
            r = "17"
        elif t <= 3053:
            r = "18"
        elif t <= 3269:
            r = "19"
        elif t <= 3485:
            r = "20"
        else:
            r = "21"
        return f"basket-{r}.wbbasket.ru/vol{t}"

    @staticmethod
    def construct_host_v2(e, t="nm", r=False):
        s = int(e)
        n = s // 10000 if t == "video" else s // 1000
        if t == "nm":
            o = Se.vol_host_v2(s)
        else:
            o = ""
        return f"https://{o}/part{n}/{s}"


# Пример использования:
