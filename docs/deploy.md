# Deploying Kalys to AWS

One `t3.medium` EC2 instance running everything: PostgreSQL in Docker, the
Chainlit app as a systemd service, and Bedrock reached through an IAM role
attached to the instance.

The app is **not** exposed to the internet. You reach it through an SSH
tunnel. That keeps port 8000 closed, which matters because every question
costs Bedrock money - an open endpoint is a way for strangers and bots to
spend it.

## Why you run the console steps, not the AI assistant

The `kalysdev` IAM user is deliberately limited to Bedrock (see CLAUDE.md).
It cannot create instances, roles, or security groups, and we are keeping it
that way: a key that can only call one API is a key that can only ever cost
one thing. So steps 1-3 below are yours, in the AWS Console.

## 1. Create the IAM role for the instance

The server must never hold a copy of your access keys. An instance role gives
it short-lived credentials automatically, and boto3 finds them with no code
change.

1. IAM -> Policies -> Create policy -> JSON, paste this, name it
   `KalysBedrockInvoke`:

   ```json
   {
       "Version": "2012-10-17",
       "Statement": [
           {
               "Effect": "Allow",
               "Action": [
                   "bedrock:InvokeModel",
                   "bedrock:InvokeModelWithResponseStream"
               ],
               "Resource": [
                   "arn:aws:bedrock:*::foundation-model/anthropic.*",
                   "arn:aws:bedrock:*:*:inference-profile/us.anthropic.*"
               ]
           }
       ]
   }
   ```

   Narrower than the laptop policy on purpose: the server only answers
   questions, so it does not need `ListFoundationModels` or
   `GetFoundationModel`. Both resource lines are needed - the model call goes
   through the `us.` cross-region inference profile, which is a separate ARN
   from the model itself (see LEARNING.md, Step 7).

2. IAM -> Roles -> Create role -> AWS service -> EC2, attach
   `KalysBedrockInvoke`, name it `KalysServerRole`.

## 2. Create the security group

EC2 -> Security Groups -> Create, name `kalys-sg`:

- Inbound: **SSH (22) from My IP only**. Nothing else. No port 8000.
- Outbound: leave the default (all traffic) - the instance needs to reach
  GitHub, PyPI, Hugging Face, and Bedrock.

## 3. Launch the instance

EC2 -> Launch instance:

| Setting | Value |
| --- | --- |
| Name | `kalys` |
| AMI | Ubuntu Server 24.04 LTS | 
| Type | `t3.medium` (4 GB RAM) |
| Key pair | create one, download the `.pem`, `chmod 400` it |
| Security group | `kalys-sg` from step 2 |
| Storage | 30 GB gp3 |
| IAM instance profile | `KalysServerRole` from step 1 (under Advanced details) |
| Region | `us-east-1` |

Why these: Ubuntu 24.04 ships Python 3.12, which the project targets.
`t3.medium` is the smallest type that fits the local embedding model next to
PostgreSQL - `t3.micro` (1 GB, the free-tier type) cannot. 30 GB holds the
OS, the 1 GB model, PyTorch, and the database with room to spare.

**It only costs money while it is running.** Stop it when you are not
testing; stopping keeps the disk and costs a few dollars a month, while
running it all the time is about $30.

## 4. Copy the database up

The site blocks cloud servers (403, see `docs/cbd-api.md`), so the server
cannot download the law itself. Move the database instead.

On your laptop, regenerate the dump if the data has changed:

```bash
docker compose up -d db
docker compose exec -T db pg_dump -U kalys -d kalys -Fc > data/deploy/kalys.dump
```

Then copy it up (about 40 MB):

```bash
scp -i <your-key.pem> data/deploy/kalys.dump ubuntu@<instance-ip>:~/kalys.dump
```

## 5. Run the setup script

```bash
ssh -i <your-key.pem> ubuntu@<instance-ip>
git clone --branch claude/kalys-v2-rebuild-d75xqd \
    https://github.com/zhanboloteraliev/kalys_v2.git
./kalys_v2/deploy/setup.sh ~/kalys.dump
```

It installs Docker, uv, and Python, starts PostgreSQL, restores the dump,
downloads the embedding model, and installs the `kalys` systemd service. It
takes several minutes, mostly the model download, and is safe to re-run.

## 6. Open the app

From your laptop:

```bash
ssh -i <your-key.pem> -L 8000:localhost:8000 ubuntu@<instance-ip>
```

Leave that running and open <http://localhost:8000>.

## Running it day to day

On the instance:

```bash
sudo journalctl -u kalys -f      # live logs
sudo systemctl restart kalys     # after a git pull
cd kalys_v2 && git pull && sudo systemctl restart kalys
```

To stop paying for compute, stop the instance in the console (**Stop**, not
Terminate - Terminate deletes the disk and the database with it). After a
start, the public IP changes unless you attach an Elastic IP, so check it
before you SSH.

## If something is wrong

| Symptom | Likely cause |
| --- | --- |
| `NoCredentialsError`, or Bedrock access denied | The instance profile is missing, or `.env` wrongly sets `AWS_PROFILE`. The server must not set it. |
| `ValidationException` about on-demand throughput | The model id lost its `us.` inference-profile prefix. |
| Service restarts in a loop | `sudo journalctl -u kalys -n 50`. Usually PostgreSQL is not up yet, or out of memory. |
| "No matching articles" for every question | The dump did not restore. Check `SELECT count(*) FROM embeddings` is 3295. |
| Tunnel connects but the page never loads | The service is not running: `sudo systemctl status kalys`. |
