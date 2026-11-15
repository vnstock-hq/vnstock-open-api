# vnstock-oapi (Vnstock Open API)

Tiếng Việt · [English](README.en.md)

Thư viện Python và công cụ dòng lệnh chạy trên máy của bạn, giúp quản lý mã khoá, mở phiên và gọi các cổng Open API chứng khoán và dữ liệu tài chính bằng tài khoản của chính bạn (mô hình BYOK).

Một phần của hệ sinh thái [Vnstock thế hệ 6](https://vnstocks.com/the-he-6).

![License: Custom (Personal Use)](https://img.shields.io/badge/License-Custom%20\(Personal%20Use\)-lightgrey)
![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue)

## Điểm chính

* **Kết nối đầu nguồn theo mô hình BYOK:** chạy trực tiếp trên máy của bạn, dùng tài khoản và mã khoá do chính bạn đăng ký tại nguồn để gọi tới cổng Open API đã chọn; không đi qua máy chủ trung gian của Vnstock.
* **Chuẩn hoá phiên đăng nhập và bảo vệ mã khoá:** gom các cơ chế xác thực (API key, cặp key + secret đổi token, ký `HMAC-SHA256`, Smart OTP dùng chung phiên giữa các tiến trình trên máy, theo dõi hạn đổi khoá) về một luồng thống nhất. Không nhận khoá qua tham số hàm; tự đọc từ biến môi trường → `.env` → `keyring` của hệ điều hành và bọc trong `Secret` để tránh lộ khoá khi làm việc cùng trợ lý AI.
* **Khảo sát nguồn ngay trên dòng lệnh:** các lệnh `status`, `check`, `ops`, `--preview`, `dump` và `terms` giúp kiểm tra khoá, xem trước yêu cầu đã che bí mật và xuất cấu trúc trường dữ liệu (`shapes.md` không chứa giá trị thật) trước khi viết mã phân tích.
* **Giữ nguyên phản hồi gốc trong `Envelope`:** mỗi lần gọi trả về dữ liệu nguyên bản kèm ngữ cảnh truy vấn và mã băm `SHA-256`, không chứa khoá xác thực, sẵn sàng lưu ra tệp `.jsonl` hoặc chuyển sang `vnstock` thế hệ 6 (`Connector`) để chuẩn hoá thành `pandas.DataFrame`.
* **Ranh giới chỉ đọc rõ ràng:** chỉ kết nối qua `HTTPS` tới đúng tên miền đã khai báo cho nguồn và chỉ thực hiện các thao tác đọc dữ liệu thị trường, danh mục mã, lịch giao dịch, sự kiện; không đọc khoá ký lệnh (`PRIVATE_KEY`), không gửi lệnh và không truy cập thông tin tài sản cá nhân.

> Đọc bài giới thiệu đầy đủ về câu chuyện thiết kế và cách kết hợp với Vnstock thế hệ 6: **[Vnstock Open API: Tự kết nối Open API bằng khoá của chính bạn](https://vnstocks.com/blog/vnstock-oapi-ket-noi-open-api-chung-khoan)**.

## Phạm vi của thư viện

| `vnstock-oapi` làm                                                                                             | `vnstock-oapi` không làm                                                                                   |
| :------------------------------------------------------------------------------------------------------------- | :--------------------------------------------------------------------------------------------------------- |
| Chạy trên máy của bạn để kết nối tới cổng Open API bằng tài khoản và khoá của chính bạn                        | Không phải bên cung cấp hay phân phối lại dữ liệu; không vận hành máy chủ trung chuyển dữ liệu thị trường  |
| Quản lý khoá trong keyring, `.env`, biến môi trường và dùng chung phiên đăng nhập giữa các tiến trình trên máy | Không nhận khoá qua tham số hàm; không gửi khoá nguồn hay số liệu thống kê sử dụng về `vnstocks.com`       |
| Thực hiện các thao tác chỉ đọc đối với dữ liệu thị trường, danh sách mã và sự kiện được hỗ trợ                 | Không gửi lệnh, không sửa/huỷ lệnh và không đọc thông tin tài khoản hay danh mục tài sản cá nhân           |
| Giữ nguyên phản hồi gốc của nguồn trong `Envelope` kèm thông tin đối chiếu và mã băm `SHA-256`                 | Không đổi tên cột, không quy đổi đơn vị hay làm phẳng dữ liệu (việc này thuộc thư viện `vnstock` thế hệ 6) |
| Công bố mã nguồn theo giấy phép riêng để bạn kiểm tra, học tập và nghiên cứu cá nhân                           | Không phải phần mềm mã nguồn mở; không thay bạn quyết định quyền lưu trữ hay chia sẻ dữ liệu của nguồn     |

## Nguồn hỗ trợ trong bản này

| Nguồn                                                                                                                                                                                       | Định danh (`source`) | Cơ chế xác thực                                                     | Biến môi trường                   |
| :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | :------------------- | :------------------------------------------------------------------ | :-------------------------------- |
| [SSI FastConnect API](https://developers.ssi.com.vn/)                                                                                                                                       | `ssi`                | Cặp key và secret đổi token; tự động làm mới khi hết hạn            | `SSI_API_KEY`, `SSI_API_SECRET`   |
| [DNSE Lightspeed API](https://developers.dnse.com.vn/docs/guide/intro/register_guide/)                                                                                                      | `dnse`               | Cặp key và secret ký `HMAC-SHA256` từng yêu cầu, không cần mở phiên | `DNSE_API_KEY`, `DNSE_API_SECRET` |
| [TCBS iFlash Open API](https://developers.tcbs.com.vn/)                                                                                                                                     | `tcbs`               | API key cùng mã Smart OTP nhập tay khi mở phiên                     | `TCBS_API_KEY`                    |
| [FHSC Open API](https://developers.fhsc.com.vn/introduction)                                                                                                                                | `fhsc`               | Một API key gửi kèm mỗi yêu cầu (theo dõi hạn đổi khoá 30 ngày)     | `FHSC_API_KEY`                    |
| [Financial Modeling Prep](https://intelligence.financialmodelingprep.com/pricing-plans?couponCode=thinhvu\&utm_source=vnstock-official-site\&utm_medium=vnstock-oapi\&utm_campaign=thinhvu) | `fmp`                | Một API key gửi kèm mỗi yêu cầu (thị trường quốc tế)                | `FMP_API_KEY`                     |
| [Binance Spot Public API](https://developers.binance.com/docs/binance-spot-api-docs/rest-api)                                                                                               | `binance`            | Không cần khoá (chỉ dùng cổng dữ liệu thị trường công khai)         | Không có                          |

> Liên kết đăng ký dịch vụ Financial Modeling Prep (FMP) ở bảng trên kèm mã giới thiệu dành cho người dùng Vnstock (ưu đãi 20% khi thanh toán gói FMP; Vnstock có thể nhận hoa hồng giới thiệu). Gói Basic không thu phí của FMP không cần qua liên kết này.

## Cài đặt và bắt đầu nhanh

Yêu cầu Python 3.10 trở lên. Cài đặt từ kho gói của Vnstock hoặc trực tiếp từ GitHub:

```bash
pip install -U --extra-index-url https://vnstocks.com/api/packages "vnstock-oapi>=0.1.0,<1"
# Hoặc cài trực tiếp từ mã nguồn trên GitHub:
pip install git+https://github.com/vnstock-hq/vnstock-open-api.git
vnstock-oapi --version
```

### 1. Trên dòng lệnh (CLI)

```bash
vnstock-oapi keys set <nguồn>                # cất khoá vào keyring (ẩn ký tự khi gõ); hỗ trợ --profile
vnstock-oapi auth <nguồn>                    # mở phiên xác thực (hỏi Smart OTP nếu nguồn yêu cầu)
vnstock-oapi status                          # kiểm tra khoá, phiên và hạn đổi khoá (không gọi mạng)
vnstock-oapi check <nguồn>                   # gọi thử một thao tác nhẹ nhất để xác nhận khoá
vnstock-oapi ops <nguồn>                     # liệt kê hàm chung, hàm riêng và endpoint gốc của nguồn
vnstock-oapi ohlc <nguồn> FPT --preview      # xem trước request sẽ gửi đi (chưa gửi, khoá đã che)
vnstock-oapi ohlc <nguồn> FPT --start 2026-10-01 -o fpt.jsonl
vnstock-oapi dump <nguồn> --symbol FPT       # gọi bộ thao tác mẫu, xuất JSONL + shapes.md (chỉ tên trường & kiểu)
vnstock-oapi terms <nguồn>                   # xem tóm tắt điều khoản và đường dẫn văn bản gốc của nguồn
```

Lệnh `dump` tạo tệp `samples/<thời điểm>/shapes.md` chỉ ghi đường dẫn trường và kiểu dữ liệu mà **không chứa bất kỳ giá trị thật nào** — tiện đưa cho coding agent viết hàm bóc tách mà không làm lộ dữ liệu từ tài khoản của bạn.

### 2. Trong Python

Chỉ `import vnstock_oapi` sẽ không gọi mạng, không đọc khoá và không ghi tệp:

```python
import vnstock_oapi as oapi

src = oapi.source("<nguồn>")
bars = src.ohlc("FPT", start="2026-10-01", end="2026-10-30", interval="1D")
bars[0].body                        # phản hồi gốc của nguồn, giữ nguyên
bars.save("fpt.jsonl")              # lưu ra tệp JSONL (quyền đọc chỉ cho tài khoản của bạn)

src.preview("ohlc", "FPT")          # xem trước request sẽ gửi (chưa gửi), khoá đã che
src.raw("<thao-tác>")               # gọi thẳng endpoint theo tên trong tài liệu của nguồn
print(src.terms)                    # tóm tắt điều khoản của nguồn kèm đường dẫn gốc
```

Kết hợp với `vnstock` thế hệ 6 để chuẩn hoá thành `pandas.DataFrame` (hoặc đóng gói phản hồi tự lấy bằng `oapi.wrap`):

```python
from vnstock import Connector

df = Connector("<nguồn>").equity("FPT").ohlcv("fpt.jsonl")
env = oapi.wrap("<nguồn>", "<thao-tác>", body=my_json, request={"symbol": "FPT"})
```

### 3. Dữ liệu trong phiên qua WebSocket

Hỗ trợ `ssi`, `dnse`, `tcbs` và `binance` trên các kênh dữ liệu thị trường công bố trong `streams.toml`:

```bash
pip install "vnstock-oapi[stream]"
vnstock-oapi stream <nguồn>     # xem danh sách các kênh dữ liệu thị trường được hỗ trợ
```

### 4. Mở rộng cho nguồn riêng (`oapi.register`)

Dùng `vnstock_oapi.ext` và `oapi.register(...)` để khai báo nguồn Open API trong gói riêng của bạn mà không sửa kho này. Nguồn đăng ký thừa hưởng cơ chế đọc khoá, chốt chặn `Transport` và định dạng `Envelope`. Xem hướng dẫn tại [docs/viet-nguon-cua-ban.md](docs/viet-nguon-cua-ban.md).

## Hàm chung và cấu trúc `Envelope`

8 hàm chung có cùng tên ở mọi nguồn hỗ trợ (báo lỗi `UnsupportedError` hoặc `InputError` tường minh khi nguồn không hỗ trợ thao tác hay khung thời gian được chọn):

| Tên hàm    | Nội dung truy xuất                                                                 |
| :--------- | :--------------------------------------------------------------------------------- |
| `ohlc`     | Dữ liệu nến lịch sử (`1m`, `3m`, `5m`, `15m`, `30m`, `1H`, `4H`, `1D`, `1W`, `1M`) |
| `trades`   | Dữ liệu khớp lệnh từng lệnh trong phiên                                            |
| `quote`    | Giá chào mua/bán và sổ lệnh mới nhất                                               |
| `index`    | Dữ liệu chỉ số thị trường                                                          |
| `foreign`  | Giao dịch của nhà đầu tư nước ngoài                                                |
| `symbols`  | Danh sách mã chứng khoán                                                           |
| `calendar` | Lịch ngày giao dịch                                                                |
| `events`   | Sự kiện doanh nghiệp và lịch thực hiện quyền                                       |

Mỗi trang phản hồi được đóng gói thành một bản ghi `Envelope` (đặc tả: [envelope.v1.json](vnstock_oapi/spec/envelope.v1.json), stream frame: [frame.v1.json](vnstock_oapi/spec/frame.v1.json)):

| Trường                               | Ý nghĩa                                                                   |
| :----------------------------------- | :------------------------------------------------------------------------ |
| `envelope_version`                   | Phiên bản định dạng (`"1"`)                                               |
| `source`, `operation`, `api_version` | Định danh nguồn, thao tác/endpoint đã gọi và phiên bản API                |
| `request`                            | Tham số đã gửi (đã lọc bỏ khoá, chữ ký, token và header xác thực)         |
| `fetched_at`, `http_status`          | Thời điểm nhận phản hồi (RFC 3339) và mã HTTP                             |
| `body`, `body_sha256`                | Phản hồi nguyên văn từ nguồn (hoặc `body_base64`) và mã băm `SHA-256`     |
| `producer`, `transport`              | Phần mềm tạo bản ghi và thông tin tên miền, giao thức, độ trễ, số request |

## Giấy phép và điều khoản của nguồn

* **Quyền dùng phần mềm ([LICENSE](LICENSE)):** `vnstock-oapi` phát hành theo giấy phép riêng của Vnstock, không phải giấy phép mã nguồn mở. Mã nguồn được công bố để bạn kiểm tra cách xử lý khoá và sử dụng cho mục đích cá nhân, học tập và nghiên cứu. Bạn được công bố và chia sẻ không thu phí gói mở rộng dùng `vnstock-oapi` làm phụ thuộc khi đáp ứng đủ điều kiện tại mục 1.1 của giấy phép ([docs/viet-nguon-cua-ban.md](docs/viet-nguon-cua-ban.md)). Phân phối lại phần mềm, dùng cho hoạt động thương mại hoặc làm sản phẩm giúp bên thứ ba truy xuất dữ liệu cần có văn bản đồng ý riêng của Vnstock.
* **Quyền đối với dữ liệu của nguồn:** dữ liệu lấy về bằng tài khoản và khoá của bạn tuân theo điều khoản của nguồn đó. Tra cứu nhanh bằng `vnstock-oapi terms <nguồn>` (chỉ để tra nhanh, không thay thế văn bản gốc và không phải ý kiến pháp lý).

## Tài liệu và phát triển

* Bài viết giới thiệu: [Vnstock Open API: Tự kết nối Open API bằng khoá của chính bạn](https://vnstocks.com/blog/vnstock-oapi-ket-noi-open-api-chung-khoan)
* Tài liệu chính thức: [Giới thiệu](https://vnstocks.com/docs/vnstock-oapi/gioi-thieu) · [Cài đặt & thiết lập khoá](https://vnstocks.com/docs/vnstock-oapi/cai-dat) · [Dòng lệnh](https://vnstocks.com/docs/vnstock-oapi/dong-lenh) · [Dùng trong Python](https://vnstocks.com/docs/vnstock-oapi/python)
* Hướng dẫn viết gói mở rộng: [docs/viet-nguon-cua-ban.md](docs/viet-nguon-cua-ban.md)

```bash
pip install -e ".[dev]"
pytest -q && ruff check . && mypy vnstock_oapi
```

## Về Vnstock

Vnstock là hệ sinh thái tự động hoá cá nhân trong nghiên cứu đầu tư: bộ công cụ Python chạy trên máy của bạn để kết nối dữ liệu, tính toán định lượng có công thức minh bạch và tự động hoá quy trình phân tích, cùng tài liệu chuẩn hoá cho trợ lý AI và cộng đồng thực hành.

* Trang chủ & Tài liệu: [vnstocks.com](https://vnstocks.com)
* GitHub: [github.com/vnstock-hq](https://github.com/vnstock-hq)

Vnstock cấp quyền dùng phần mềm, không bán dữ liệu; dữ liệu có sẵn và chính xác tới đâu là do nguồn. Nội dung và phần mềm phục vụ nghiên cứu, cung cấp theo hiện trạng, không phải khuyến nghị đầu tư.
