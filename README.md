# vnstock-oapi

> **Ngôn ngữ / Language:** Tiếng Việt | [English](README.en.md)  
> **Tài liệu chính thức / Official Docs:** [vnstocks.com/docs/vnstock-oapi](https://vnstocks.com/docs/vnstock-oapi)

Cổng chung để đăng nhập và gọi Open API của nhiều nguồn tài chính bằng tài khoản của chính bạn: thử một API trong
vài phút, dựng giải pháp của riêng mình trên đó, hoặc đưa dữ liệu vào các luồng xử lý tự động của Vnstock.

Một phần của hệ sinh thái Vnstock.

![License: Custom (Personal Use)](https://img.shields.io/badge/License-Custom%20(Personal%20Use)-lightgrey)
![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue)

## Dùng vnstock-oapi để làm gì

**1. Không phải tự viết lại phần kết nối cho từng nguồn.** Cất key, đăng nhập, giữ phiên, lấy lại token khi hết
hạn, gọi hết các trang, chờ khi bị giới hạn tốc độ, đọc thông báo lỗi: những việc này đã được làm sẵn và kiểm thử
cho mọi nguồn. Bạn dùng lại, không phải viết lại, và không phải sửa ở nhiều nơi khi nguồn thay đổi.

**2. Thử nhanh một API trước khi quyết định dùng.** Có key là kiểm tra được ngay: key có hợp lệ không, request gửi
đi trông thế nào, nguồn trả về những trường gì, endpoint nào gọi được với tài khoản của bạn. Không phải đọc hết
tài liệu, không phải dựng project riêng chỉ để gọi thử.

**3. Có nền dữ liệu thô sạch cho bước xử lý tiếp theo.** Mọi kết quả về cùng một cấu trúc, giữ nguyên dữ liệu gốc
và ghi rõ nguồn gốc. Bạn tự viết phần phân tích của mình trên đó, hoặc đưa vào các thư viện trong hệ sinh thái
Vnstock để chuẩn hoá và xử lý nâng cao trong các luồng chạy tự động.

## Vì sao cần một cổng chung

Ngày càng nhiều nguồn mở Open API cho chính khách hàng của họ, nhưng mỗi nơi làm một kiểu:

- Nơi chỉ có REST API và tài liệu, nơi phát hành thêm SDK hay thư viện Python riêng.
- Nơi chỉ cần một API key. Nơi cần cặp key và secret để lấy token, token hết hạn thì phải xin lại. Nơi bắt ký
  từng request. Nơi đòi mã OTP mỗi lần đăng nhập.
- Cách chia trang, báo lỗi, giới hạn số lần gọi và thời hạn đăng nhập cũng mỗi nơi một khác.

Mỗi người tự viết code kết nối riêng thì cả cộng đồng cùng viết đi viết lại những đoạn giống nhau, và mỗi bản lại
trả dữ liệu theo một hình dạng khác, khó dùng chung. `vnstock-oapi` gom phần đó về một chỗ: thêm một nguồn chỉ cần
một tệp mô tả (địa chỉ API, kiểu xác thực, danh sách endpoint, cách đặt tên tham số), phần còn lại dùng chung.

## Hướng phát triển

Mục tiêu của `vnstock-oapi` là trở thành chuẩn kết nối chung cho các Open API mà người dùng có quyền truy cập, và là
đầu vào cho các bước xử lý phía sau trong hệ sinh thái Vnstock. Chuẩn này gồm ba phần, đều có trong kho này:

| Phần | Nằm ở | Làm gì |
| --- | --- | --- |
| Các kiểu xác thực | `vnstock_oapi/core/auth.py` | Gom các cách đăng nhập phổ biến (API key, key và secret, ký request, OTP) thành vài kiểu dùng chung. Nguồn mới chọn một kiểu có sẵn, không phải tự viết |
| Mô tả từng nguồn | `vnstock_oapi/sources/<nguồn>.py`, `vnstock_oapi/spec/sources/<nguồn>/` | Endpoint, tham số, cách nguồn viết mã chỉ số hay khung thời gian, tóm tắt điều khoản |
| Định dạng kết quả | `vnstock_oapi/spec/envelope.v1.json` | Mỗi lần gọi API cho ra một bản ghi có cùng cấu trúc: dữ liệu gốc của nguồn kèm thông tin cho biết nó đến từ đâu |

```text
 nguồn A: một API key           ─┐                                    ┌─▶ code phân tích của bạn
 nguồn B: key + secret → token  ─┼─▶ vnstock-oapi ─▶ bản ghi kết quả ─┤
 nguồn C: OTP mỗi lần đăng nhập ─┘                  (dữ liệu gốc)     └─▶ chuẩn hoá, xử lý tự động trong Vnstock
                                                                          (đang phát triển)
```

Bản ghi kết quả là chỗ nối giữa phần kết nối và phần xử lý. Theo hướng phát triển của Vnstock, thư viện `vnstock`
sẽ đọc thẳng các bản ghi này để chuẩn hoá thành bảng dữ liệu, và các thư viện tự động hoá, tính toán định lượng
trong hệ sinh thái nhận bảng đó để chạy tiếp theo lịch. Khi đó, thêm một nguồn vào `vnstock-oapi` cũng là thêm một
nguồn cho toàn bộ quy trình phía sau, không phải viết thêm đoạn kết nối nào.

Bản hiện tại làm phần kết nối: dữ liệu giữ nguyên như nguồn trả về, chưa đổi tên cột, chưa chuyển thành bảng.

## vnstock-oapi là gì, không là gì

| Là | Không là |
| --- | --- |
| Thư viện Python và công cụ dòng lệnh chạy trên máy của bạn | Không phải nhà cung cấp dữ liệu. Dữ liệu thuộc về nguồn |
| Công cụ giúp bạn tự lấy dữ liệu qua Open API, bằng tài khoản và key của chính bạn | Không phải máy chủ trung gian. Request đi thẳng từ máy bạn tới nguồn bạn chọn |
| Chỉ đọc: dữ liệu thị trường, danh sách mã, sự kiện doanh nghiệp | Không đụng tới lệnh hay tài khoản. Không có hàm nào làm việc đó |
| Trả về dữ liệu gốc của nguồn, kèm thông tin nguồn gốc | Không phải bộ chuẩn hoá. Việc đó thuộc thư viện `vnstock` |
| Phần mềm nghiên cứu, cung cấp theo hiện trạng | Không phải khuyến nghị đầu tư |

Thư viện không gọi tới vnstocks.com và không gửi số liệu thống kê sử dụng. Trên máy bạn, nó lưu phiên đăng nhập
trong keyring của hệ điều hành (hoặc một tệp chỉ tài khoản của bạn đọc được), và ghi tệp khi bạn gọi `save()`.

## Cài đặt

Cần Python 3.10 trở lên. Cài từ mã nguồn trên GitHub, hoặc từ kho gói chung của hệ sinh thái Vnstock:

```console
pip install git+https://github.com/vnstock-hq/vnstock-open-api.git
pip install -U --extra-index-url https://vnstocks.com/api/packages vnstock-oapi
vnstock-oapi --version
```

Đã tải kho về máy thì chạy `pip install .` trong thư mục kho.

## Giấy phép

`vnstock-oapi` phát hành theo giấy phép riêng của Vnstock, không phải giấy phép mã nguồn mở. Mã nguồn được để ở
đây để bạn đọc và tự kiểm tra cách thư viện xử lý key, phiên đăng nhập và dữ liệu trên máy mình.

- Được dùng cho cá nhân, học tập và nghiên cứu.
- Được công bố và chia sẻ miễn phí gói mở rộng của bạn dùng `vnstock-oapi` làm phụ thuộc, theo các điều kiện ở mục
  1.1 của giấy phép: không đóng gói kèm mã `vnstock-oapi`, không thu phí, mỗi người dùng tự kết nối bằng key của
  mình, không đặt lệnh hay lấy thông tin tài khoản, ghi rõ nguồn gốc. Xem
  [docs/viet-nguon-cua-ban.md](docs/viet-nguon-cua-ban.md) (bản tiếng Anh: [docs/write-your-own-source.md](docs/write-your-own-source.md)).
- Phân phối lại phần mềm, dùng cho hoạt động thương mại, hoặc làm sản phẩm mà giá trị chính là giúp người khác lấy
  dữ liệu, cần có văn bản đồng ý riêng của Vnstock.
- Quyền dùng phần mềm không bao gồm quyền dùng dữ liệu của nguồn. Dữ liệu bạn lấy về tuân theo điều khoản bạn đã
  đồng ý với nguồn đó.

Điều khoản đầy đủ: [LICENSE](LICENSE).

## Thử nhanh một nguồn

Có key là thử được ngay, không cần viết dòng code nào. Thay `<nguồn>` bằng tên nguồn bạn chọn trong bảng
[Nguồn có trong bản này](#nguồn-có-trong-bản-này):

```console
vnstock-oapi keys set <nguồn>                # lưu key vào keyring, không hiện ký tự khi gõ
vnstock-oapi check <nguồn>                   # key có dùng được không: gọi thử một endpoint nhẹ
vnstock-oapi ops <nguồn>                     # nguồn có những hàm và endpoint nào
vnstock-oapi ohlc <nguồn> FPT --preview      # xem request sẽ gửi đi, chưa gửi, key đã được che
vnstock-oapi ohlc <nguồn> FPT --start 2026-10-01 -o fpt.jsonl
vnstock-oapi dump <nguồn> --symbol FPT       # gọi một loạt endpoint mẫu, báo cáo cái nào chạy được
```

`dump` ghi vào `samples/<thời điểm>/`:

- `<nguồn>/<tên>.jsonl`: kết quả của từng endpoint.
- `shapes.md`: các trường và kiểu dữ liệu của mọi phản hồi, **không kèm giá trị**. Đọc tệp này là biết nguồn trả
  về những gì để thiết kế bước xử lý tiếp theo.
- `manifest.json`: endpoint nào chạy được, mã HTTP, lỗi nếu có.

Các tệp `.jsonl` là dữ liệu thật lấy bằng tài khoản của bạn: để trên máy, đừng commit hay chia sẻ.

Trong Python:

```python
import vnstock_oapi as oapi

src = oapi.source("<nguồn>")      # nguồn bạn chọn
bars = src.ohlc("FPT", start="2026-10-01", end="2026-10-30", interval="1D")
bars[0].body              # dữ liệu nguồn trả về, giữ nguyên
bars[0].operation         # endpoint đã gọi
bars.save("fpt.jsonl")    # lưu ra tệp JSONL, mỗi dòng một bản ghi

src.preview("ohlc", "FPT")        # xem trước request sẽ gửi đi (chưa gửi), key đã được che
src.raw("<thao-tác>")             # gọi thẳng endpoint theo tên trong tài liệu của nguồn; danh sách: src.ops
print(src.terms)                  # tóm tắt điều khoản của nguồn, kèm đường dẫn văn bản gốc
```

Chỉ `import` thì thư viện chưa gọi mạng, chưa đọc key, chưa ghi tệp nào.

## Nguồn có trong bản này

| Nguồn | Tên trong thư viện | Cách xác thực | Biến môi trường |
| --- | --- | --- | --- |
| SSI FastConnect API | `ssi` | Key và secret để lấy token, tự lấy lại khi hết hạn | `SSI_API_KEY`, `SSI_API_SECRET` |
| DNSE Lightspeed API | `dnse` | Key và secret, ký từng request, không cần đăng nhập | `DNSE_API_KEY`, `DNSE_API_SECRET` |
| TCBS iFlash Open API | `tcbs` | API key cùng mã Smart OTP gõ tay khi đăng nhập; phiên tối đa 24 giờ | `TCBS_API_KEY` |
| FHSC Open API | `fhsc` | Một API key gửi kèm mỗi request | `FHSC_API_KEY` |
| Financial Modeling Prep | `fmp` | Một API key gửi kèm mỗi request | `FMP_API_KEY` |
| Binance (dữ liệu thị trường công khai) | `binance` | Không cần key | Không có |

FMP là dữ liệu thị trường quốc tế, không có thị trường Việt Nam. Binance chỉ dùng cổng dữ liệu thị trường công
khai mà Binance công bố cho việc gọi API không cần key. Xem nguồn nào hỗ trợ những gì: `vnstock-oapi ops <nguồn>`.

Nguồn cần OTP (xem cột "Cách xác thực") mở phiên một lần, rồi mọi công cụ trên máy dùng chung trong thời hạn
của phiên:

```console
vnstock-oapi auth <nguồn>       # hỏi mã OTP, không hiện ký tự khi gõ
```

OTP không bao giờ nằm trong biến môi trường hay tệp.

Thư viện tìm key theo thứ tự: biến môi trường, tệp `.env` ở thư mục đang chạy, rồi keyring. Không hàm nào nhận
key qua tham số. Những key mà nguồn cấp cho việc khác ngoài đọc dữ liệu thì thư viện không bao giờ đọc tới.

## Hàm dùng chung cho mọi nguồn

Cùng một tên hàm, gọi được ở mọi nguồn có hỗ trợ. Nguồn không hỗ trợ thì báo lỗi `UnsupportedError` chứ không
trả về kết quả rỗng.

| Hàm | Lấy gì |
| --- | --- |
| `ohlc` | Dữ liệu nến (giá mở, cao, thấp, đóng, khối lượng) |
| `trades` | Khớp lệnh từng lệnh |
| `quote` | Giá và sổ lệnh mới nhất |
| `index` | Dữ liệu chỉ số |
| `foreign` | Giao dịch của nhà đầu tư nước ngoài |
| `symbols` | Danh sách mã |
| `calendar` | Ngày giao dịch |
| `events` | Sự kiện doanh nghiệp |

Tham số chung: `symbol`, `start`, `end` (giờ không ghi múi giờ thì hiểu là giờ Việt Nam), `interval`
(`1m 3m 5m 15m 30m 1H 4H 1D 1W 1M`, mặc định `1D`), `limit`, `asset`. Nguồn không có khung thời gian bạn chọn thì
báo lỗi `InputError`; thư viện không tự gộp nến. Nguồn trả dữ liệu theo nhiều trang thì thư viện tự lấy hết, mỗi
trang thành một bản ghi.

Ngoài các hàm chung, bạn vẫn gọi được mọi endpoint gốc của nguồn qua `raw()`, và các hàm riêng của từng nguồn
qua `vnstock-oapi call <nguồn> <hàm>`.

## Dữ liệu trong phiên qua WebSocket

Với DNSE, SSI, TCBS và Binance, thư viện nhận dữ liệu thị trường trong phiên qua cổng WebSocket mà nguồn công bố,
cập nhật theo độ trễ của nguồn. Chỉ mở các kênh dữ liệu thị trường; kênh lệnh và tài khoản bị chặn ngay trong thư viện.

```console
pip install ".[stream]"
vnstock-oapi stream <nguồn>     # xem các kênh được phép
```

## Kết quả trả về

Mỗi lần gọi API cho ra một bản ghi (trong code gọi là `Envelope`): dữ liệu gốc của nguồn được giữ nguyên, đi kèm
thông tin để biết nó đến từ đâu, lấy lúc nào, và đối chiếu lại được về sau. Một hàm có thể trả về nhiều bản ghi
khi nguồn chia trang.

| Trường | Nội dung |
| --- | --- |
| `envelope_version` | Phiên bản định dạng, hiện là `"1"` |
| `source`, `operation`, `api_version` | Nguồn, endpoint đã gọi, phiên bản API |
| `request` | Tham số đã gửi. Không bao giờ chứa key, chữ ký, token hay header |
| `fetched_at`, `http_status` | Thời điểm lấy, mã HTTP |
| `body` | Dữ liệu nguồn trả về, giữ nguyên. Nếu không phải JSON thì nằm ở `body_base64` |
| `body_sha256` | Mã băm SHA-256 của dữ liệu gốc, dùng để kiểm tra dữ liệu có bị sửa không |
| `producer` | Phần mềm đã tạo bản ghi |
| `transport` | Tên miền, giao thức, độ trễ, số request đã gửi |

Nếu bạn đã lấy dữ liệu bằng SDK chính thức của nguồn hay bằng code của riêng mình, bạn vẫn đóng gói được kết quả
về cùng định dạng này:

```python
env = oapi.wrap("<nguồn>", "<thao-tác>", body=my_json, request={"symbol": "FPT"})
```

Đặc tả đầy đủ: [`vnstock_oapi/spec/envelope.v1.json`](vnstock_oapi/spec/envelope.v1.json).

## Bảo mật

- Key nằm trong biến môi trường, `.env` hoặc keyring; không lộ ra khi in đối tượng, khi dùng f-string hay khi
  pickle.
- Mọi request đi qua một chỗ duy nhất: bật đầy đủ kiểm tra TLS, chỉ gọi tới tên miền đã khai cho nguồn, chỉ dùng
  request đọc, có giới hạn thời gian chờ.
- Các công cụ chạy trên cùng một máy dùng chung một phiên đăng nhập, nên notebook và tác vụ chạy định kỳ không
  làm phiên của nhau hết hiệu lực.
- Thư viện không có đường nào nhận mật khẩu tài khoản. Nguồn chỉ cho đăng nhập bằng mật khẩu tài khoản thì không
  được hỗ trợ, vì mật khẩu đó mở cả tài sản trong tài khoản.
- Đừng dán key vào notebook, mã nguồn hay khung chat với trợ lý AI.

## Điều khoản của nguồn

Đọc điều khoản của nguồn trước khi dùng. Thư viện có sẵn bản tóm tắt ngắn, ghi số điều và đường dẫn tới văn bản
gốc:

```console
vnstock-oapi terms <nguồn>
```

Bản tóm tắt chỉ để tra nhanh, không thay cho văn bản gốc và không phải ý kiến pháp lý.

## Thêm một nguồn

Một nguồn mới gồm tệp `vnstock_oapi/sources/<nguồn>.py` mô tả nguồn, thư mục `vnstock_oapi/spec/sources/<nguồn>/`
chứa `mapping.toml` (cách nguồn viết các giá trị) và `terms.toml` (tóm tắt điều khoản), và một kiểu xác thực chọn
từ các kiểu có sẵn. Nguồn nào cũng phải chạy qua cùng bộ kiểm thử với máy chủ giả lập.

Muốn tự khai một nguồn trong gói của riêng bạn mà không sửa kho này: dùng `vnstock_oapi.ext` và gọi
`oapi.register(...)`. Nguồn đó chạy dưới cùng sàn an toàn (chỉ `https`, chỉ host đã khai, chỉ đọc dữ liệu thị
trường), và phong bì ghi tên gói của bạn. Xem [docs/viet-nguon-cua-ban.md](docs/viet-nguon-cua-ban.md) (tiếng Anh: [docs/write-your-own-source.md](docs/write-your-own-source.md)).

Muốn đề xuất nguồn mới hay báo lỗi kết nối, mở issue trên GitHub của kho này. Ghi kèm tên endpoint, tham số và
thông báo lỗi; đừng gửi key, token hay dữ liệu thật lấy từ tài khoản của bạn.

## Phát triển

```console
pip install -e ".[dev]"
pytest -q && ruff check . && mypy vnstock_oapi
```

Xem danh sách lệnh: `vnstock-oapi --help`. Xem nguồn hỗ trợ những gì: `vnstock-oapi ops <nguồn>`.

## Về Vnstock

Vnstock là hệ sinh thái tự động hoá cá nhân trong nghiên cứu đầu tư: bộ công cụ Python chạy trên máy của bạn để
kết nối dữ liệu, tính toán định lượng có công thức minh bạch và tự động hoá quy trình phân tích, cùng trợ lý AI
viết code đúng ngay lần đầu.

- Tài liệu chính thức: [vnstocks.com/docs/vnstock-oapi](https://vnstocks.com/docs/vnstock-oapi)
- Trang web: [vnstocks.com](https://vnstocks.com)
- GitHub: [github.com/vnstock-hq](https://github.com/vnstock-hq)

Vnstock là công cụ nghiên cứu, cung cấp theo hiện trạng, không phải khuyến nghị đầu tư.
