# NEW - ver 2026!
## 1. EC2에서 S3 다운로드
cd ~
aws s3 sync s3://hgi-ai-res/hjjo/aws/ ~/aws/

### 2. 초기 세팅 (최초 1회, 리부트 포함)
cd aws
chmod +x setup-ec2.sh
sudo ./setup-ec2.sh
#### → Phase 1 완료 → 자동 리부트 → Phase 2 자동 실행

## 3. Phase 2 완료 확인
tail -f /var/log/ec2-setup.log

## 4. 이미지 빌드 (Phase 2에서 자동 빌드되지만, 코드 변경 시 재빌드)
cd ~/aws
docker compose build

## 5. 메인 사용자 컨테이너 실행
docker compose up -d

## 6. 접속
ssh -p 5000 hgiai@3.35.12.44

## 6-1. code-server (브라우저 VS Code) — 폐쇄망/내부망용
# 로컬 PC에서 SSM 포트 포워딩 실행 (AWS CLI 필요)
aws ssm start-session \
  --target i-<INSTANCE_ID> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["5001"],"localPortNumber":["8443"]}'

# 브라우저에서 http://localhost:8443 접속
# 로그인 패스워드 = 컨테이너의 PASSWORD (SSH와 동일)
# 다른 사용자 컨테이너: portNumber를 5011/5021/5031/... 로 변경

## 6-2. VS Code 확장(vsix) 추가
# 1) 인터넷 되는 PC에서 Marketplace → .vsix 다운로드
#    예: https://marketplace.visualstudio.com/items?itemName=ms-python.python
# 2) vsix/ 폴더에 .vsix 파일 넣기
# 3) docker compose build --no-cache (빌드 타임에 자동 설치)

## 6-3. 운영계 (root, 폐쇄망)
# .env 에서 USERNAME=root 로 변경
# → 사용자 생성/홈 셋업 스킵, root 홈(/root) 기준으로 code-server 기동
# → code-server 포트는 LLM_CODE_SERVER_PORT 값 그대로 사용
# → /root 는 호스트 /volume/root 에 영속화 (bash history, code-server 설정 재시작 보존)
docker compose up -d
# 접속은 위 6-1 SSM 포트 포워딩과 동일 (root로 SSH 접속은 불가)

## 7. 추가 사용자 (필요 시) - 여러 예시들 참고
chmod +x ~/aws/user.sh

sudo ~/aws/user.sh down hjjo
sudo ~/aws/user.sh down jin
sudo ~/aws/user.sh down song
sudo ~/aws/user.sh down mail

sudo ~/aws/user.sh up hjjo --password 1106 --gpus all
sudo ~/aws/user.sh up jin --password 1234 --gpus all
sudo ~/aws/user.sh up song --password 1234 --gpus 2,3
sudo ~/aws/user.sh up mail-agent --password 1234 --gpus 2,3
sudo ~/aws/user.sh up cho --password 1234 --gpus 0
sudo ~/aws/user.sh up jeon --password 1234 --gpus 2,3
sudo ~/aws/user.sh up min --password 1234 --gpus 3


## 8. 코드 변경 반영 시
aws s3 sync s3://hgi-ai-res/hjjo/aws/ ~/aws/
chmod +x ~/aws/setup-ec2.sh ~/aws/user.sh
docker compose build --no-cache && docker compose up -d
sudo ~/aws/user.sh rebuild
sudo ~/aws/user.sh rebuild hjjo
cd ~/aws && docker compose build --no-cache && sudo ~/aws/user.sh rebuild 

# ============================================
# LLM 개발서버 (AWS EC2 g6e.12xlarge)
# ============================================

Host AWS-jin
    HostName 3.35.12.44
    Port 5020
    User jin
    # 추가 포트: 5021-5029

Host AWS-song
    HostName 3.35.12.44
    Port 5030
    User song
    # 추가 포트: 5031-5039

Host AWS-mail
    HostName 3.35.12.44
    Port 5040
    User mail
    # 추가 포트: 5041-5049