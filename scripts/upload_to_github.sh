#!/bin/bash
# رفع كل الملفات لـ GitHub

TOKEN="REDACTED_TOKEN"
REPO="azammaza770309310-boop/whatsapp-link-monitor"
DOWNLOAD_DIR="/home/z/my-project/download"

# قائمة الملفات للرفع
declare -A FILES=(
  ["monitor_v6.py"]="monitor_v6.py"
  ["requirements.txt"]="requirements.txt"
  ["accounts.env.example"]="accounts.env.example"
  ["README.md"]="README.md"
  [".gitignore"]=".gitignore"
  ["export_session.py"]="export_session.py"
)

for local_name in "${!FILES[@]}"; do
  remote_name="${FILES[$local_name]}"
  file_path="$DOWNLOAD_DIR/$local_name"
  
  echo "📤 رفع: $local_name → $remote_name"
  
  # Convert file to base64
  content_base64=$(base64 -w 0 "$file_path")
  
  # GitHub API URL
  api_url="https://api.github.com/repos/$REPO/contents/$remote_name"
  
  # JSON payload
  json_payload=$(jq -n --arg msg "Add $remote_name" --arg content "$content_base64" '{message: $msg, content: $content}')
  
  # Upload
  response=$(curl -s -X PUT \
    -H "Authorization: token $TOKEN" \
    -H "Accept: application/vnd.github.v3+json" \
    "$api_url" \
    -d "$json_payload")
  
  # Check result
  if echo "$response" | grep -q '"sha"'; then
    echo "   ✅ نجح"
  else
    echo "   ❌ فشل: $response" | head -3
  fi
  echo ""
done

echo "=================================================="
echo "🎉 تم رفع جميع الملفات!"
echo "=================================================="
echo ""
echo "📂 المستودع: https://github.com/$REPO"
