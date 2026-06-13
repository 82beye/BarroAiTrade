-- 단일 이메일 1통 발송(범용). Mail.app(iCloud), 일반첨부.
-- argv: <to> <subject> <body> [attach1] [attach2] ...
-- 첨부 합계는 호출측에서 <~14MB(인코딩 후 <20MB)로 제한 → iCloud Mail Drop 미발동.
-- 성공 시 "sent", 실패 시 "ERROR <num>: <msg>" 반환.
on run argv
	set theTo to item 1 of argv
	set theSubject to item 2 of argv
	set theBody to item 3 of argv
	set atts to {}
	if (count of argv) > 3 then
		repeat with i from 4 to count of argv
			set end of atts to (item i of argv)
		end repeat
	end if
	tell application "Mail"
		with timeout of 180 seconds
			set newMsg to make new outgoing message with properties {subject:theSubject, content:theBody, visible:false}
			tell newMsg
				make new to recipient at end of to recipients with properties {address:theTo}
				repeat with p in atts
					make new attachment with properties {file name:(POSIX file (p as text))} at after the last paragraph
				end repeat
			end tell
			delay 10
			try
				send newMsg
			on error errMsg number errNum
				return "ERROR " & errNum & ": " & errMsg
			end try
		end timeout
	end tell
	return "sent"
end run
