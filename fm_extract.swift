import Foundation
import FoundationModels

@main
struct FMExtract {
    static func main() async {
        let data = FileHandle.standardInput.readDataToEndOfFile()
        guard let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let text = obj["text"] as? String,
              let now = obj["now"] as? String else {
            print("{\"error\":\"bad input\"}")
            return
        }
        let model = SystemLanguageModel.default
        guard case .available = model.availability else {
            let reason = "\(model.availability)".replacingOccurrences(of: "\"", with: "'")
            print("{\"error\":\"model unavailable: \(reason)\"}")
            return
        }
        let instructions = """
        你是日程抽取助手。当前时间：\(now)，时区 Asia/Shanghai。
        从用户给的文本中抽取明确提到的日程、会议、任务截止时间，只输出 JSON，不要输出任何解释、markdown 代码块标记或其他文字。
        JSON 格式：{"events": [{"title": "简短标题，用原文任务关键词", "year": 2026, "month": 1, "day": 1, "hour": 0, "minute": 0, "isAllDay": false, "location": "地点或空字符串", "confidence": 0.9}]}
        规则：
        1. 只抽取有明确时间指向的安排；"改天""找时间""有空"这类模糊表述不算。
        2. 相对时间（明天、下周三、今晚八点）按当前时间换算成绝对日期。
        3. 未写年份取最近的未来日期。
        4. 只有日期没有时刻的设 isAllDay=true，hour=0，minute=0。
        5. "X点前交/发"这类截止表述，时间就是该截止时刻。
        6. 没有日程就输出 {"events": []}。
        """
        do {
            let session = LanguageModelSession(instructions: instructions)
            let result = try await session.respond(to: text)
            print(result.content)
        } catch {
            let msg = "\(error)".replacingOccurrences(of: "\"", with: "'")
            print("{\"error\":\"\(msg)\"}")
        }
    }
}
