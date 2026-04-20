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