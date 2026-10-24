-- RoadPush 참조 레이어 v2 (서울 보행 링크 · 도로구간 · 보도 구간 + 성남 표준노드링크) 스키마
-- v1을 이미 넣었어도 그대로 다시 실행하면 됨 (아래 ALTER가 컬럼을 맞춤)
-- 대상: Supabase PostgreSQL 15+ (PostGIS 불필요, 좌표는 WGS84 위경도/WKT 텍스트)
-- 운영 규칙(docs/DB.md): 모든 테이블 RLS ON, 정책 없음 → Data API로 노출 안 됨. 백엔드는 postgres 역할로 접속.
BEGIN;
CREATE TABLE IF NOT EXISTS ref_road_segment (
  segment_id       varchar(24) PRIMARY KEY,   -- SIG_CD-RDS_MAN_NO (도로명주소 도로구간)
  sig_cd           varchar(5)  NOT NULL,
  road_name        varchar(80),
  road_name_cd     varchar(12),
  road_class       varchar(12) CHECK (road_class IN ('expressway','daero','ro','gil')),
  is_main          boolean     NOT NULL,      -- 본선(true) / 종속구간(false)
  is_underground   boolean     NOT NULL DEFAULT false, -- 지하철역 통로·지하상가·지하차도
  length_m         real,
  width_notice_m   real,                       -- 도로명주소 고시폭 ROAD_BT (대표값)
  width_rw_m       real,                       -- 실폭도로 면에서 계산한 폭
  width_ngii_m     real,                       -- 국토지리정보원 연속수치지형도 RVWD
  lanes            smallint,                   -- 국토지리정보원 RDLN
  width_m          real,                       -- 채택 도로폭
  width_source     varchar(8)  CHECK (width_source IN ('ngii','rw','notice')),
  width_agreement  varchar(16) CHECK (width_agreement IN ('all_agree','disagree','insufficient')),
  center_lat       double precision,
  center_lng       double precision
);
CREATE TABLE IF NOT EXISTS ref_walk_link (
  link_id              varchar(20) PRIMARY KEY, -- 서울시 도보 네트워크 LNKG_ID (표준노드링크 LINK_ID 아님)
  sgg_nm               varchar(10),
  emd_nm               varchar(20),
  link_type_cd         varchar(4),
  length_m             real,
  is_shared_with_car   boolean NOT NULL,
  is_crosswalk         boolean NOT NULL,
  is_overpass          boolean NOT NULL,
  is_underpass         boolean NOT NULL,
  is_bridge            boolean NOT NULL,
  is_indoor_candidate  boolean NOT NULL DEFAULT false, -- 지하·실내 후보(사진 매칭 제외용)
  underground_flag     varchar(32) CHECK (underground_flag IN ('original_attr','passage_overlap','passage_near_no_surface_road')),
  underground_passage_name varchar(80),
  road_name            varchar(80),
  road_class           varchar(12),
  road_width_m         real,
  road_width_source    varchar(8)  CHECK (road_width_source IN ('ngii','rw','notice')),
  lanes                smallint,
  sidewalk_width_m     real,
  sidewalk_width_source varchar(16) CHECK (sidewalk_width_source IN ('building_no','route_approx')),
  grade_mobility       varchar(2)  CHECK (grade_mobility IN ('L0','L1','L2','L3','L4')),
  grade_mobility_basis varchar(24) CHECK (grade_mobility_basis IN ('sidewalk_width','shared_road_width','overpass_underpass')),
  grade_pedestrian     varchar(2)  CHECK (grade_pedestrian IN ('P0','P1','P2')),
  unjudged_reason      varchar(40),
  center_lat           double precision,
  center_lng           double precision,
  geom_wkt             text
);
CREATE TABLE IF NOT EXISTS ref_sidewalk_section (
  section_id     integer PRIMARY KEY,
  route_no       varchar(10),
  manager        varchar(20),
  route_name     varchar(80),
  direction      varchar(80),
  section_text   text,
  length_m       real,
  width_m        real,
  block_type     varchar(80),
  built_year     smallint,
  cross_section  varchar(40),
  match_method   varchar(16) CHECK (match_method IN ('building_no','route_approx','failed'))
);
CREATE TABLE IF NOT EXISTS ref_seongnam_link (
  link_id varchar(20) PRIMARY KEY,  -- 국토교통부 표준노드링크 LINK_ID (백엔드 sites.link_id와 같은 키)
  sgg_nm varchar(10), f_node varchar(10), t_node varchar(10),
  road_name varchar(100), road_rank varchar(3),
  is_urban_expressway boolean NOT NULL,
  length_m real, lanes_one_dir smallint, lanes_total smallint,
  road_width_m real, road_width_source varchar(10),
  has_median boolean, is_oneway boolean,
  has_sidewalk boolean NOT NULL,
  sidewalk_left boolean NOT NULL, sidewalk_left_width_m real,
  sidewalk_right boolean NOT NULL, sidewalk_right_width_m real,
  sidewalk_width_m real, sidewalk_width_source varchar(30),
  is_backstreet boolean NOT NULL,
  grade_mobility varchar(2) CHECK (grade_mobility IN ('L0','L1','L2','L3','L4')),
  grade_pedestrian varchar(2) CHECK (grade_pedestrian IN ('P0','P1','P2')),
  grade_basis varchar(30), unjudged_reason varchar(30),
  center_lat double precision NOT NULL, center_lng double precision NOT NULL,
  geom_wkt text NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ref_seongnam_link_grade ON ref_seongnam_link (grade_mobility);
CREATE INDEX IF NOT EXISTS ix_ref_seongnam_link_backstreet ON ref_seongnam_link (is_backstreet);
-- v1 → v2 업그레이드 (이미 테이블이 있을 때만 의미 있음)
ALTER TABLE ref_road_segment ADD COLUMN IF NOT EXISTS is_underground boolean NOT NULL DEFAULT false;
ALTER TABLE ref_walk_link ADD COLUMN IF NOT EXISTS is_indoor_candidate boolean NOT NULL DEFAULT false;
ALTER TABLE ref_walk_link ADD COLUMN IF NOT EXISTS underground_flag varchar(32);
ALTER TABLE ref_walk_link ADD COLUMN IF NOT EXISTS underground_passage_name varchar(80);
ALTER TABLE ref_walk_link DROP COLUMN IF EXISTS tree_count;          -- 가로수 원본 공공누리 4유형(변경금지) → 가공값 삭제
ALTER TABLE ref_walk_link DROP COLUMN IF EXISTS effective_width_m;
CREATE INDEX IF NOT EXISTS ix_walk_link_sgg      ON ref_walk_link (sgg_nm);
CREATE INDEX IF NOT EXISTS ix_walk_link_grade_m  ON ref_walk_link (grade_mobility);
CREATE INDEX IF NOT EXISTS ix_walk_link_latlng   ON ref_walk_link (center_lat, center_lng);
CREATE INDEX IF NOT EXISTS ix_road_seg_name      ON ref_road_segment (road_name);
CREATE INDEX IF NOT EXISTS ix_road_seg_latlng    ON ref_road_segment (center_lat, center_lng);
ALTER TABLE ref_road_segment     ENABLE ROW LEVEL SECURITY;
ALTER TABLE ref_walk_link        ENABLE ROW LEVEL SECURITY;
ALTER TABLE ref_sidewalk_section ENABLE ROW LEVEL SECURITY;
ALTER TABLE ref_seongnam_link    ENABLE ROW LEVEL SECURITY;
COMMIT;
