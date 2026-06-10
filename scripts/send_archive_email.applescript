on run argv
	set f1 to item 1 of argv
	set f2 to item 2 of argv
	set f3 to item 3 of argv
	set dt to item 4 of argv
	set theSubject to "BarroAiTrade EOD " & dt & " — OHLCV(일봉/5분봉)+매매로그"
	set theBody to "BarroAiTrade " & dt & " 장마감 데이터 아카이브 3종 첨부." & return & "- 일봉 OHLCV" & return & "- 5분봉 OHLCV" & return & "- 매매로그(data/)" & return & return & "(대용량 첨부는 iCloud Mail Drop 링크로 전송될 수 있음)"
	tell application "Mail"
		set newMsg to make new outgoing message with properties {subject:theSubject, content:theBody, visible:false}
		tell newMsg
			make new to recipient at end of to recipients with properties {address:"82beye@gmail.com"}
			make new attachment with properties {file name:(POSIX file f1)} at after the last paragraph
			make new attachment with properties {file name:(POSIX file f2)} at after the last paragraph
			make new attachment with properties {file name:(POSIX file f3)} at after the last paragraph
		end tell
		delay 20
		send newMsg
	end tell
	return "sent"
end run
