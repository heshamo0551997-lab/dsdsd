(function () {
  function getToken() {
    return (
      localStorage.getItem('admin_token') ||
      localStorage.getItem('token') ||
      sessionStorage.getItem('admin_token') ||
      sessionStorage.getItem('token') ||
      ''
    );
  }

  function closeModal() {
    var el = document.getElementById('changePwdModal');
    if (el) el.remove();
  }

  async function submitChange() {
    var token = getToken();
    var oldPassword = document.getElementById('cp_old').value.trim();
    var newPassword = document.getElementById('cp_new').value.trim();
    var confirmPassword = document.getElementById('cp_confirm').value.trim();

    if (!oldPassword || !newPassword || !confirmPassword) {
      alert('请填写完整');
      return;
    }
    if (newPassword !== confirmPassword) {
      alert('两次输入的新密码不一致');
      return;
    }
    if (newPassword.length < 6) {
      alert('新密码至少 6 位');
      return;
    }

    try {
      var res = await fetch('/api/admin/auth/change-password', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ' + token
        },
        body: JSON.stringify({
          old_password: oldPassword,
          new_password: newPassword
        })
      });

      var data = await res.json();
      if (data.code === 0) {
        alert('密码修改成功，请重新登录');
        localStorage.removeItem('admin_token');
        localStorage.removeItem('token');
        sessionStorage.removeItem('admin_token');
        sessionStorage.removeItem('token');
        closeModal();
        location.reload();
      } else {
        alert(data.message || '修改失败');
      }
    } catch (e) {
      alert('请求失败：' + (e && e.message ? e.message : e));
    }
  }

  function openModal() {
    var token = getToken();
    if (!token) {
      alert('请先登录后台');
      return;
    }
    if (document.getElementById('changePwdModal')) return;

    var wrap = document.createElement('div');
    wrap.id = 'changePwdModal';
    wrap.style.position = 'fixed';
    wrap.style.left = '0';
    wrap.style.top = '0';
    wrap.style.right = '0';
    wrap.style.bottom = '0';
    wrap.style.background = 'rgba(0,0,0,.45)';
    wrap.style.zIndex = '100000';
    wrap.style.display = 'flex';
    wrap.style.alignItems = 'center';
    wrap.style.justifyContent = 'center';

    wrap.innerHTML =
      '<div style="width:360px;background:#fff;border-radius:12px;padding:20px;box-shadow:0 12px 30px rgba(0,0,0,.2);">' +
      '<div style="font-size:22px;font-weight:700;margin-bottom:16px;">修改管理员密码</div>' +
      '<input id="cp_old" type="password" placeholder="旧密码" style="width:100%;height:40px;margin-bottom:10px;padding:0 12px;box-sizing:border-box;border:1px solid #ddd;border-radius:6px;">' +
      '<input id="cp_new" type="password" placeholder="新密码" style="width:100%;height:40px;margin-bottom:10px;padding:0 12px;box-sizing:border-box;border:1px solid #ddd;border-radius:6px;">' +
      '<input id="cp_confirm" type="password" placeholder="确认新密码" style="width:100%;height:40px;margin-bottom:16px;padding:0 12px;box-sizing:border-box;border:1px solid #ddd;border-radius:6px;">' +
      '<div style="display:flex;gap:10px;justify-content:flex-end;">' +
      '<button id="cp_cancel" style="padding:8px 16px;border:none;border-radius:6px;background:#9ca3af;color:#fff;cursor:pointer;">取消</button>' +
      '<button id="cp_submit" style="padding:8px 16px;border:none;border-radius:6px;background:#2563eb;color:#fff;cursor:pointer;">确认修改</button>' +
      '</div>' +
      '</div>';

    document.body.appendChild(wrap);
    document.getElementById('cp_cancel').onclick = closeModal;
    document.getElementById('cp_submit').onclick = submitChange;
  }

  // Add to sidebar or menu if needed
  // This is usually called from index.html or a dedicated button
  window.openChangePwdModal = openModal;
})();