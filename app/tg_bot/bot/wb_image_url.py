def image_url(e, t='c516x688'):
    s = int(e)
    return f"{img_server.construct_host_v2(s)}/images/{t}/"

class ImgServer:
    @staticmethod
    def vol_host_v2(e):
        if 0 <= e <= 143:
            t = "01"
        elif e <= 287:
            t = "02"
        elif e <= 431:
            t = "03"
        elif e <= 719:
            t = "04"
        elif e <= 1007:
            t = "05"
        elif e <= 1061:
            t = "06"
        elif e <= 1115:
            t = "07"
        elif e <= 1169:
            t = "08"
        elif e <= 1313:
            t = "09"
        elif e <= 1601:
            t = "10"
        elif e <= 1655:
            t = "11"
        elif e <= 1919:
            t = "12"
        elif e <= 2045:
            t = "13"
        elif e <= 2189:
            t = "14"
        elif e <= 2405:
            t = "15"
        elif e <= 2621:
            t = "16"
        else:
            t = "17"
        return f"basket-{t}.wbbasket.ru"

    @staticmethod
    def vol_feedback_host(e):
        if 0 <= e <= 431:
            t = "01"
        elif e <= 863:
            t = "02"
        elif e <= 1199:
            t = "03"
        elif e <= 1535:
            t = "04"
        elif e <= 1919:
            t = "05"
        elif e <= 2303:
            t = "06"
        else:
            t = "07"
        return f"feedback{t}.wbbasket.ru"

    @staticmethod
    def vol_video_host(e):
        if 0 <= e <= 11:
            t = "01"
        elif e <= 23:
            t = "02"
        elif e <= 35:
            t = "03"
        elif e <= 47:
            t = "04"
        elif e <= 59:
            t = "05"
        elif e <= 71:
            t = "06"
        elif e <= 83:
            t = "07"
        elif e <= 95:
            t = "08"
        elif e <= 107:
            t = "09"
        elif e <= 119:
            t = "10"
        elif e <= 131:
            t = "11"
        elif e <= 143:
            t = "12"
        else:
            t = "13"
        return f"videonme-basket-{t}.wbbasket.ru"

    @staticmethod
    def construct_host_v2(e, t="nm"):
        r = int(e)
        a = r % 144 if t == "video" else r // 100000
        n = r // 10000 if t == "video" else r // 1000

        if t == "nm":
            o = ImgServer.vol_host_v2(a)
        elif t == "feedback":
            o = ImgServer.vol_feedback_host(a)
        elif t == "video":
            o = ImgServer.vol_video_host(a)
        else:
            raise ValueError("Invalid host type")

        return f"https://{o}/vol{a}/part{n}/{r}"

# Создаем экземпляр img_server
img_server = ImgServer()