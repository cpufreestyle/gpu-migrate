param([string]$Title, [string]$Message)
try {
    $null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
    $null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime]

    $t = [System.Security.SecurityElement]::Escape($Title)
    $m = [System.Security.SecurityElement]::Escape($Message)
    $xml_text = "<toast><visual><binding template=`"ToastGeneric`"><text>$t</text><text>$m</text></binding></visual></toast>"

    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $xml.LoadXml($xml_text)
    $toast = New-Object Windows.UI.Notifications.ToastNotification $xml
    $app_id = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app_id).Show($toast)
} catch { }
