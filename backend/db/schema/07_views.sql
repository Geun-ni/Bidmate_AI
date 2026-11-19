-- 07. 화면용 조회 뷰 (여러 영역 참조 → 모든 테이블 정의 이후에 생성)
-- 쓰는 모듈: 없음 (뷰)
-- 읽는 모듈: (예정) 추천 목록 API ← 프론트 메인 대시보드(UI-01)
-- 의존: 03_bid_collection.sql, 05_evaluation.sql, 06_notice_workflow.sql
-- 뷰는 데이터를 갖지 않으므로 재초기화 때 지우고 다시 만들어 정의 변경을 반영한다.

DROP VIEW IF EXISTS v_recommendation;

-- 추천 목록 후보: 진행 중인 한전 적격심사 공사용역 공고 + 판정 결과 + 별표 여부 (MISMATCH 제외는 API에서)
-- progressState 는 마감 후에도 'AttendProgress'로 남는 경우가 많아 입찰참가신청 마감 일시도 확인한다.
CREATE VIEW v_recommendation AS
SELECT e.company_id, n.notice_no, n.title, n.place_name, n.work_type,
       n.attend_req_close_at, n.bid_end_at, n.presumed_price,
       e.overall_status, e.reason,
       EXISTS (SELECT 1 FROM favorite_notice f
               WHERE f.company_id = e.company_id AND f.notice_no = n.notice_no) AS is_favorite
FROM bid_notice n
JOIN company_bid_eligibility e ON e.notice_no = n.notice_no
WHERE n.company_id_src = 'COM01'
  AND n.purchase_type = 'ConstructionService'
  AND n.bid_type = 'QualifiedEval'
  AND n.is_open = 1
  AND (n.attend_req_close_at IS NULL OR n.attend_req_close_at >= datetime('now', 'localtime'));
