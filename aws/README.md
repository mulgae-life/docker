# AWS EC2 GPU 서버 (vLLM + 다중 사용자)

## 0. 로컬 → S3 업로드 (최초/코드 변경 시)
```bash
cd /workspace/docker/aws
aws s3 sync . s3://hgi-ai-res/hjjo/aws/
```

## 1. EC2 → S3 다운로드
```bash
cd ~
aws s3 sync s3://hgi-ai-res/hjjo/aws/ ~/aws/
```

## 2. 초기 세팅 (최초 1회, 리부트 포함)
```bash
cd ~/aws
cp .env.example .env      # 값 수정 (USERNAME, PASSWORD, VOLUME_DEVICE 등)
chmod +x setup-ec2.sh
sudo ./setup-ec2.sh
# Phase 1 → 자동 리부트 → Phase 2 자동 실행
```

## 3. Phase 2 진행 확인
```bash
tail -f /var/log/ec2-setup.log
```

## 4. 이미지 빌드 + 메인 컨테이너 실행
```bash
cd ~/aws
docker compose build
docker compose up -d
```

## 5. SSH 접속
```bash
ssh -p 5000 <user>@<host>
```

## 6. code-server (브라우저 VS Code) — 폐쇄망/내부망
로컬 PC에서 SSM 포트 포워딩:
```bash
aws ssm start-session \
  --target i-<INSTANCE_ID> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["7777"],"localPortNumber":["8443"]}'
```
브라우저 `http://localhost:8443` 접속 (비밀번호 = `.env`의 `PASSWORD`).
- 메인 컨테이너: `portNumber` = `LLM_CODE_SERVER_PORT` 값 (기본 7777)
- user.sh 사용자 컨테이너: `portNumber` = 각 사용자 포트 범위의 첫 포트 (5011/5021/5031/…)

## 7. 추가 사용자 관리
```bash
chmod +x ~/aws/user.sh

sudo ~/aws/user.sh up hjjo --password 1106 --gpus all
sudo ~/aws/user.sh up jin --password 1234 --gpus all
sudo ~/aws/user.sh up song --password 1234 --gpus 2,3
sudo ~/aws/user.sh up mail-agent --password 1234 --gpus 2,3
sudo ~/aws/user.sh up cho --password 1234 --gpus 0
sudo ~/aws/user.sh up jeon --password 1234 --gpus 2,3
sudo ~/aws/user.sh up min --password 1234 --gpus 3

sudo ~/aws/user.sh list

sudo ~/aws/user.sh down hjjo
sudo ~/aws/user.sh down jin
sudo ~/aws/user.sh down song
sudo ~/aws/user.sh down mail
```
포트 자동 할당: 사용자별 10포트씩 (첫 사용자 5010(SSH) + 5011–5019, 두 번째 5020 + 5021–5029, …)

## 8. VS Code 확장 추가 (폐쇄망용)
1. 인터넷 되는 PC에서 Marketplace → `.vsix` 다운로드
2. `~/aws/vsix/` 폴더에 `.vsix` 파일 넣기
3. `docker compose build --no-cache` (빌드 타임에 자동 설치)

## 9. 운영계 모드 (root 컨테이너, 폐쇄망)
```bash
# .env 에서 USERNAME=root 로 변경
docker compose up -d
```
- 사용자 생성/홈 셋업 스킵, `/root` 홈 기준으로 code-server 동반 기동
- `/root` 는 호스트 `/volume/root` 에 영속화 (bash history, code-server 설정 보존)
- SSH 접속 불가 → **6번 SSM 포트 포워딩으로만 접속**

## 10. 코드 변경 반영
```bash
# (로컬에서 0번 S3 업로드 먼저)
aws s3 sync s3://hgi-ai-res/hjjo/aws/ ~/aws/
chmod +x ~/aws/setup-ec2.sh ~/aws/user.sh

cd ~/aws
docker compose build --no-cache && docker compose up -d
sudo ~/aws/user.sh rebuild          # 전체 사용자 컨테이너 재생성
sudo ~/aws/user.sh rebuild hjjo     # 특정 사용자만
```

---

## 참고: SSH config (`~/.ssh/config`)
```
Host AWS-main
    HostName <host>
    Port 5000
    User <user>

Host AWS-hjjo
    HostName <host>
    Port 5010
    User hjjo
    # 추가 포트: 5011-5019

Host AWS-jin
    HostName <host>
    Port 5020
    User jin
    # 추가 포트: 5021-5029
```
